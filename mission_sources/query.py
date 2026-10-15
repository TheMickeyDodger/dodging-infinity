"""Assemble one versioned query response from the registry, every
registered source, and one injected runtime snapshot value.

Data-driven by construction: the sources read are exactly the registry's
records, each read by the reader for its KIND, and the missions reported
are exactly what each fresh source's store holds at ``now``. Registering a
source, or creating a mission in a registered store, changes the next
response with no code change.

Honest degradation: a registry that cannot be loaded — including one that
does not exist (``registry_absent``: never configured, or deleted) — is
reported with its own health and problem (and no sources), never as an
empty, healthy registry; only a registry file that was actually read is
``fresh``.

Expiry: every response carries ``valid_until``, the earliest expiry of
everything it presents as live (``valid_until`` below);
``contract.response_freshness`` is the consumer operation that enforces it.

Bounded: one query reads at most ``MAX_REGISTERED_SOURCES`` sources, each
store file once within ``MAX_STORE_READ_BYTES``, all of them together
within ``MAX_QUERY_INPUT_BYTES``, and builds at most ``MAX_QUERY_MISSIONS``
mission views; the runtime snapshot is bounded by its own contract limits.
Every bound is charged BEFORE the read or the expansion, a source that
would exceed one is listed with an explicit problem, and every bound is
exposed in the response's ``limits`` block. ``min_poll_interval_seconds``
is the declared polling contract for consumers; this pure query reads no
clock and cannot poll. A degraded source is listed with its health and contributes no
missions. A runtime snapshot that is not ``fresh`` yields no members and
no binding (``unverified``). ``contract.response_freshness`` lets a
consumer refuse a response that is stale, from an unknown contract, or
built over a degraded registry.
"""

from mission_sources import contract
from mission_sources import fileread
from mission_sources import registry as registry_module
from mission_sources import roster
from mission_sources import sources as sources_module

_REGISTRY_PROBLEM_HEALTH = {
    registry_module.PROBLEM_REGISTRY_ABSENT: contract.HEALTH_UNAVAILABLE,
    registry_module.PROBLEM_REGISTRY_UNREADABLE: contract.HEALTH_UNAVAILABLE,
    registry_module.PROBLEM_REGISTRY_PERMISSIONS: contract.HEALTH_UNAVAILABLE,
    registry_module.PROBLEM_REGISTRY_MALFORMED: contract.HEALTH_MALFORMED,
    registry_module.PROBLEM_REGISTRY_INCOMPATIBLE: contract.HEALTH_INCOMPATIBLE,
}


def limits():
    """Every hard bound a consumer needs to judge a response, exposed."""
    return {
        "max_sources": registry_module.MAX_REGISTERED_SOURCES,
        "max_registry_bytes": registry_module.MAX_REGISTRY_BYTES,
        "max_json_depth": fileread.MAX_JSON_DEPTH,
        "max_store_read_bytes": sources_module.MAX_STORE_READ_BYTES,
        "max_query_input_bytes": sources_module.MAX_QUERY_INPUT_BYTES,
        "max_query_missions": sources_module.MAX_QUERY_MISSIONS,
        "max_snapshot_tasks": contract.MAX_SNAPSHOT_TASKS,
        "max_task_bindings": contract.MAX_TASK_BINDINGS,
        "max_snapshot_agents": contract.MAX_SNAPSHOT_AGENTS,
        "min_poll_interval_seconds": contract.MIN_POLL_INTERVAL_SECONDS,
    }


def _registry_block(health, problem, source_count, directory,
                    serialized_bytes=None):
    return {
        "health": health,
        "problem": problem,
        "directory": directory,
        "source_count": source_count,
        "max_sources": registry_module.MAX_REGISTERED_SOURCES,
        "serialized_bytes": serialized_bytes,
        "max_serialized_bytes": registry_module.MAX_REGISTRY_BYTES,
    }


def valid_until(now, max_age_seconds, reading, source_blocks):
    """The earliest expiry of everything the response presents as live.

    The envelope's own bound, every FRESH source's registered bound (each
    was read at ``now``), and — when the runtime snapshot is fresh — the
    snapshot's own ``observed_at + max_age_seconds``. A degraded source or
    snapshot presents nothing as live and does not shorten it."""
    candidates = [now + max_age_seconds]
    for block in source_blocks:
        if block["health"] == contract.HEALTH_FRESH:
            candidates.append(block["observed_at"] + block["max_age_seconds"])
    if reading.is_live():
        candidates.append(reading.observed_at + reading.max_age_seconds)
    return min(candidates)


def assemble(registry_document, runtime_snapshot, now,
             max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS, readers=None,
             registry_directory=None, registry_bytes=None,
             observed_roots=None, confirmed_bindings=None):
    """One response over an already loaded (and validated) registry.
    ``observed_roots``, ``confirmed_bindings``: see ``roster.classify``."""
    registry_module.validate_document(registry_document)
    reading = contract.read_runtime_snapshot(runtime_snapshot, now,
                                             max_age_seconds)
    source_blocks = []
    views = []
    budget = sources_module.QueryBudget()
    for source in sorted(registry_document["sources"],
                         key=lambda s: s["source_id"]):
        result = sources_module.read_source(source, now, budget, readers)
        if result["health"] == contract.HEALTH_FRESH:
            views.extend(result["missions"])
        block = dict(result)
        del block["missions"]
        source_blocks.append(block)
    missions, unmatched = roster.classify(views, reading, observed_roots,
                                          confirmed_bindings)
    registry = _registry_block(contract.HEALTH_FRESH, None,
                               len(registry_document["sources"]),
                               registry_directory, registry_bytes)
    return contract.envelope(
        now, max_age_seconds,
        valid_until(now, max_age_seconds, reading, source_blocks), limits(),
        registry, source_blocks, reading.describe(), missions, unmatched)


def query(source_registry, runtime_snapshot, now,
          max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS, readers=None,
          observed_roots=None, confirmed_bindings=None):
    """Load ``source_registry`` (a ``SourceRegistry``) read-only and
    assemble one response; a registry that cannot be loaded degrades
    explicitly instead of raising or reading as empty."""
    try:
        document, size = source_registry.load_measured(absent_is_empty=False)
    except registry_module.RegistryError as exc:
        reading = contract.read_runtime_snapshot(runtime_snapshot, now,
                                                 max_age_seconds)
        registry = _registry_block(
            _REGISTRY_PROBLEM_HEALTH.get(exc.problem,
                                         contract.HEALTH_MALFORMED),
            exc.problem, None, source_registry.directory)
        return contract.envelope(
            now, max_age_seconds,
            valid_until(now, max_age_seconds, reading, []), limits(),
            registry, [], reading.describe(), [], None)
    return assemble(document, runtime_snapshot, now, max_age_seconds,
                    readers, source_registry.directory, size, observed_roots,
                    confirmed_bindings)
