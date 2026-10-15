"""The explicit, durable, inspectable registry of mission sources.

A source is registered as DATA: one record naming a store directory, its
kind, the producer that writes it, and the freshness bound its readings
are held to. A newly registered source of an existing kind, and every
mission later created in any registered store, appear in the next query
with no code change. Only a new KIND needs code (a reader in
``mission_sources.sources``).

File: ``<directory>/mission_sources.json`` (0600, written only through
``workflow_authority.atomic.atomic_write_json`` under
``exclusive_store_lock`` with the lock file ``mission_sources.lock``).
Shape::

    {"mission_sources_registry_schema_version": 1,
     "sources": [{"source_id": "<id>", "kind": "mission_core",
                  "store_dir": "/abs/dir", "producer": "<label>",
                  "max_age_seconds": 60, "registered_at": <int>}]}

Rules, all refuse-never-repair:

- ``source_id``: ``[a-z0-9][a-z0-9._-]*``, at most
  ``MAX_SOURCE_ID_CHARS``; it is the qualifier of every mission
  identity read through the source, so two sources can never collide.
- ``kind``: one of ``contract.SOURCE_KINDS``.
- ``store_dir``: absolute and already normalised; the same store (by
  real path) can be registered only ONCE, under any id, so no mission
  is ever read twice.
- ``producer``: the declared producer of that store (for example
  ``grok_bot`` or ``local_request``), free text within the bound. It is
  declared provenance, reported as such and never confused with what a
  record itself records.
- ``max_age_seconds``: the freshness bound for readings of this source.
- At most ``MAX_REGISTERED_SOURCES`` sources (128); registering one more
  is refused with ``registry_full``, never silently dropped, and the bound
  is reported in every query response.
- At most ``MAX_REGISTRY_BYTES`` serialised: EVERY rewrite (registration and
  removal, through the one ``_write``) whose resulting file (exactly as
  written, ASCII-escaped) would exceed it is refused with
  ``registry_serialized_size_exceeded`` BEFORE anything is written, so the
  registry can always be read back and a refusal preserves the prior bytes;
  the current size is reported in every query response.

Re-registering an identical source is idempotent (``unchanged``);
registering a different source under an existing id is refused. Loading
is read-only: it takes no lock, creates no directory and writes nothing.
A registry file that is group- or other-accessible is refused, because a
writable registry would let anyone add a source. The file is read through
``mission_sources.fileread`` (no-follow, regular file only, at most
``MAX_REGISTRY_BYTES``): only a path that does not exist is the empty
registry; every other failure is an explicit ``RegistryError``.
"""

import json
import os
import re

from workflow_authority.atomic import atomic_write_json, exclusive_store_lock

from mission_sources import contract
from mission_sources import fileread

REGISTRY_FILE_NAME = "mission_sources.json"
REGISTRY_LOCK_FILE_NAME = "mission_sources.lock"
REGISTRY_SCHEMA_VERSION = 1
REGISTRY_VERSION_KEY = "mission_sources_registry_schema_version"
REGISTRY_KEYS = (REGISTRY_VERSION_KEY, "sources")
SOURCE_KEYS = ("source_id", "kind", "store_dir", "producer",
               "max_age_seconds", "registered_at")

# Usable bound, chosen with headroom over observed use (one source per
# DI mission state directory plus the WorkflowStore). Reaching it is an
# explicit ``registry_full`` refusal and every response exposes it; a
# source is never silently dropped.
MAX_REGISTERED_SOURCES = 128
MAX_REGISTRY_BYTES = 262144
MAX_SOURCE_ID_CHARS = 64
MAX_PRODUCER_CHARS = 128
MAX_STORE_DIR_CHARS = 1024

_SOURCE_ID_PATTERN = r"[a-z0-9][a-z0-9._-]*"

PROBLEM_REGISTRY_UNREADABLE = "registry_unreadable"
PROBLEM_REGISTRY_ABSENT = "registry_absent"
PROBLEM_REGISTRY_MALFORMED = "registry_malformed"
PROBLEM_REGISTRY_INCOMPATIBLE = "registry_incompatible"
PROBLEM_REGISTRY_PERMISSIONS = "registry_permissions_open"
PROBLEM_SOURCE_INVALID = "source_invalid"
PROBLEM_SOURCE_ID_CONFLICT = "source_id_conflict"
PROBLEM_STORE_ALREADY_REGISTERED = "store_already_registered"
PROBLEM_REGISTRY_FULL = "registry_full"
PROBLEM_REGISTRY_SIZE = "registry_serialized_size_exceeded"

OUTCOME_REGISTERED = "registered"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_REMOVED = "removed"
OUTCOME_ABSENT = "absent"


class RegistryError(ValueError):
    """The registry or a proposed source is refused. ``problem`` is stable."""

    def __init__(self, problem, message):
        super(RegistryError, self).__init__(message)
        self.problem = problem


def default_document():
    return {REGISTRY_VERSION_KEY: REGISTRY_SCHEMA_VERSION, "sources": []}


def _invalid(message):
    raise RegistryError(PROBLEM_SOURCE_INVALID, message)


def validate_source(source, location="source"):
    """Validate one source record; returns it unchanged or raises."""
    if type(source) is not dict or set(source) != set(SOURCE_KEYS):
        _invalid("%s must be an object with exactly the keys %s"
                 % (location, list(SOURCE_KEYS)))
    source_id = source["source_id"]
    if type(source_id) is not str or len(source_id) > MAX_SOURCE_ID_CHARS \
            or not re.fullmatch(_SOURCE_ID_PATTERN, source_id):
        _invalid("%s.source_id must match [a-z0-9][a-z0-9._-]* within %d"
                 " characters" % (location, MAX_SOURCE_ID_CHARS))
    if source["kind"] not in contract.SOURCE_KINDS:
        _invalid("%s.kind must be one of %s"
                 % (location, list(contract.SOURCE_KINDS)))
    store_dir = source["store_dir"]
    if type(store_dir) is not str or not store_dir \
            or len(store_dir) > MAX_STORE_DIR_CHARS or "\x00" in store_dir \
            or not os.path.isabs(store_dir) \
            or os.path.normpath(store_dir) != store_dir:
        _invalid("%s.store_dir must be an absolute, normalised path within"
                 " %d characters" % (location, MAX_STORE_DIR_CHARS))
    producer = source["producer"]
    if type(producer) is not str or not producer or "\x00" in producer \
            or len(producer) > MAX_PRODUCER_CHARS:
        _invalid("%s.producer must be a non-empty string within %d"
                 " characters" % (location, MAX_PRODUCER_CHARS))
    try:
        contract.require_max_age(source["max_age_seconds"],
                                 location + ".max_age_seconds")
        contract.require_int(source["registered_at"],
                             location + ".registered_at")
    except contract.ContractError as exc:
        _invalid(str(exc))
    return source


def serialized_size(document):
    """Exact byte length of ``document`` as ``atomic_write_json`` writes it
    (``json.dump`` with ``sort_keys=True, indent=1``, ASCII-escaped)."""
    return len(json.dumps(document, sort_keys=True, indent=1).encode("utf-8"))


def require_serialized_size(document):
    """Refuse, BEFORE any write, a document that the bounded reader could not
    read back: a registry larger than ``MAX_REGISTRY_BYTES`` would otherwise
    be written and then fail to load, losing every source at once."""
    size = serialized_size(document)
    if size > MAX_REGISTRY_BYTES:
        raise RegistryError(PROBLEM_REGISTRY_SIZE,
                            "the registry would serialise to %d bytes; the"
                            " bound is %d; nothing was written"
                            % (size, MAX_REGISTRY_BYTES))
    return size


def _store_key(source):
    return (source["kind"], os.path.realpath(source["store_dir"]))


def validate_document(document):
    """Validate a whole registry document; raises ``RegistryError``."""
    if type(document) is not dict:
        raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                            "the registry must be a JSON object")
    version = document.get(REGISTRY_VERSION_KEY)
    if type(version) is not int or version != REGISTRY_SCHEMA_VERSION:
        raise RegistryError(PROBLEM_REGISTRY_INCOMPATIBLE,
                            "registry schema version %r is not %d"
                            % (version, REGISTRY_SCHEMA_VERSION))
    if set(document) != set(REGISTRY_KEYS):
        raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                            "registry keys must be exactly %s"
                            % list(REGISTRY_KEYS))
    sources = document["sources"]
    if type(sources) is not list or len(sources) > MAX_REGISTERED_SOURCES:
        raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                            "registry sources must be a list of at most %d"
                            % MAX_REGISTERED_SOURCES)
    seen_ids, seen_stores = set(), set()
    for index, source in enumerate(sources):
        try:
            validate_source(source, "sources[%d]" % index)
        except RegistryError as exc:
            raise RegistryError(PROBLEM_REGISTRY_MALFORMED, str(exc))
        if source["source_id"] in seen_ids or _store_key(source) in seen_stores:
            raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                                "sources[%d] duplicates a source id or store"
                                % index)
        seen_ids.add(source["source_id"])
        seen_stores.add(_store_key(source))
    return document


class SourceRegistry(object):
    """The registry file in one explicit directory (no default location)."""

    def __init__(self, directory):
        if type(directory) is not str or not os.path.isabs(directory):
            raise RegistryError(PROBLEM_SOURCE_INVALID,
                                "the registry directory must be absolute")
        self.directory = directory
        self.path = os.path.join(directory, REGISTRY_FILE_NAME)

    def load(self, absent_is_empty=True):
        """The registry document (see ``load_measured``)."""
        return self.load_measured(absent_is_empty)[0]

    def load_measured(self, absent_is_empty=True):
        """``(document, bytes_read)``. Read-only: no lock, no directory
        creation, no write.

        ``absent_is_empty`` is for REGISTRATION only: a registry that does
        not exist yet starts from the empty document so the first source
        can be registered. A query passes ``False``: an absent registry
        (never configured, or deleted) is then ``registry_absent``, never
        observed-empty evidence. A symlink, special file, oversized or
        open-permission file, a file swapped during the read, invalid JSON
        or a refused document always raise ``RegistryError``: none of them
        can present as an empty, healthy registry."""
        try:
            data = fileread.read_private_file(self.path, MAX_REGISTRY_BYTES)
        except fileread.FileRefused as exc:
            if exc.problem == fileread.PROBLEM_FILE_ABSENT:
                if not absent_is_empty:
                    raise RegistryError(PROBLEM_REGISTRY_ABSENT,
                                        "registry %s does not exist" % self.path)
                return default_document(), 0
            if exc.problem == fileread.PROBLEM_FILE_PERMISSIONS_OPEN:
                raise RegistryError(PROBLEM_REGISTRY_PERMISSIONS,
                                    "registry %s is group- or other-accessible"
                                    % self.path)
            if exc.problem == fileread.PROBLEM_FILE_TOO_LARGE:
                raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                                    "registry %s exceeds %d bytes"
                                    % (self.path, MAX_REGISTRY_BYTES))
            raise RegistryError(PROBLEM_REGISTRY_UNREADABLE,
                                "registry %s refused (%s)"
                                % (self.path, exc.problem))
        try:
            document = fileread.load_bounded_json(data)
        except fileread.JsonRefused as exc:
            raise RegistryError(PROBLEM_REGISTRY_MALFORMED,
                                "registry %s refused (%s)"
                                % (self.path, exc.problem))
        return validate_document(document), len(data)

    def sources(self):
        return [dict(s) for s in self.load()["sources"]]

    def register(self, source_id, kind, store_dir, producer, max_age_seconds,
                 now):
        """Add one source; idempotent for an identical source."""
        candidate = validate_source({
            "source_id": source_id, "kind": kind, "store_dir": store_dir,
            "producer": producer, "max_age_seconds": max_age_seconds,
            "registered_at": now,
        })
        with exclusive_store_lock(self.directory, REGISTRY_LOCK_FILE_NAME):
            document = self.load()
            for existing in document["sources"]:
                same = all(existing[k] == candidate[k] for k in SOURCE_KEYS
                           if k != "registered_at")
                if existing["source_id"] == source_id:
                    if same:
                        return {"outcome": OUTCOME_UNCHANGED,
                                "source": dict(existing)}
                    raise RegistryError(PROBLEM_SOURCE_ID_CONFLICT,
                                        "source id %r is registered with"
                                        " different content" % source_id)
                if _store_key(existing) == _store_key(candidate):
                    raise RegistryError(PROBLEM_STORE_ALREADY_REGISTERED,
                                        "that %s store is already registered"
                                        " as %r" % (kind, existing["source_id"]))
            if len(document["sources"]) >= MAX_REGISTERED_SOURCES:
                raise RegistryError(PROBLEM_REGISTRY_FULL,
                                    "the registry holds %d sources; the bound"
                                    " is %d" % (len(document["sources"]),
                                                MAX_REGISTERED_SOURCES))
            document["sources"].append(candidate)
            document["sources"].sort(key=lambda s: s["source_id"])
            self._write(document)
        return {"outcome": OUTCOME_REGISTERED, "source": dict(candidate)}

    def unregister(self, source_id):
        """Remove one source by id; ``absent`` when it was not registered."""
        with exclusive_store_lock(self.directory, REGISTRY_LOCK_FILE_NAME):
            document = self.load()
            kept = [s for s in document["sources"]
                    if s["source_id"] != source_id]
            if len(kept) == len(document["sources"]):
                return {"outcome": OUTCOME_ABSENT, "source_id": source_id}
            document["sources"] = kept
            self._write(document)
        return {"outcome": OUTCOME_REMOVED, "source_id": source_id}

    def _write(self, document):
        """THE one registry rewrite (registration AND removal): validate the
        document, refuse it if its serialised form (as ``atomic_write_json``
        writes it) would exceed what ``load`` can read back, and only then
        replace the file atomically. A refusal leaves the prior bytes
        untouched. Removal can GROW the file — a compact, raw-UTF-8 registry
        that loads fine re-serialises ASCII-escaped — so it is guarded
        exactly like registration."""
        validate_document(document)
        require_serialized_size(document)
        atomic_write_json(self.directory, self.path, document,
                          temp_prefix=".mission_sources-")
