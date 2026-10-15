"""The shared, versioned query contract of ``mission_sources``: closed
vocabularies, hard bounds, the freshness / health model, and validation
of the injected runtime snapshot.

Consumers. ``response_freshness`` is the ONE operation a consumer uses to
decide whether anything in a response may be presented as live; a
response's ``valid_until`` already folds in the expiry of every inner
observation it presents as live. See ``response_freshness`` for the rule.

Readiness publication (``mission_sources.readiness``). Readiness for the
proof contract's resources is published READY only with an actual
observation time and only when that observation stays valid for the whole
contract bound; otherwise it is published NOT_READY with a truthful reason.
Documented limitation: a source (or snapshot, or compatibility observation)
whose own freshness bound is shorter than the contract's readiness bound
can never be published READY for that resource
(``inner_horizon_shorter_than_contract_bound``).

Versioning. Every query response carries ``contract`` and
``contract_version`` (``QUERY_CONTRACT`` / ``QUERY_CONTRACT_VERSION``). A
consumer that does not recognise both refuses the response
(``response_freshness`` reports ``incompatible``); an additive change
keeps the version, any other change bumps it.

Lifecycle models are never merged. A mission view carries its OWN model
identifier and its native lifecycle literal verbatim. The only shared
field is ``standing`` (active / inactive / terminal / unknown_future): a
membership GATE derived per model by that model's own table, not a
lifecycle. Two missions with the same standing are not in the same
state, and nothing here maps one model's literal onto the other's.

Health. Every source and the runtime snapshot carry exactly one of
``fresh``, ``stale``, ``unavailable``, ``malformed``, ``incompatible``,
plus a problem code. Only ``fresh`` may support a live claim; the other
four are explicit degradations that never render live and never yield a
binding.

The runtime snapshot is an injected VALUE (the ``coordination``
observation pattern): this package defines its shape and validates it;
the producer that reads the live engineering runtime (Herdr) lives
outside this package. Its shape, version 1::

    {"schema": "di.mission_sources.runtime_snapshot", "version": 1,
     "observed_at": <int epoch seconds>, "producer": <str>,
     "tasks": [{"task_id": <str>, "workspace_realpath": <str>,
                "bindings": [{"logical": <str>, "role": <str|None>,
                              "agent": <str>, "session": <str|None>,
                              "pane_id": <str|None>,
                              "workspace_id": <str|None>}]}],
     "agents": [{"agent": <str>, "provider": <str|None>,
                 "status": <str>, "session": <str|None>,
                 "pane_id": <str|None>, "workspace_id": <str|None>}]}

``tasks`` are the canonical task records and the role bindings recorded
for each task; ``agents`` are the live agents the runtime reports. Every
key is required (closed set); ``None`` means "the producer does not
know", never "empty". Validation refuses and never repairs: a wrong
type, an unknown key, a ``bool`` where an ``int`` is expected, an
oversized list or string, or an ``observed_at`` in the future makes the
whole snapshot ``malformed``. A different ``schema`` or ``version`` makes
it ``incompatible``. Duplicates are NOT refused here: they are kept so
the roster can classify them as ambiguous instead of picking one.

Nothing here reads a clock, a file or the network: ``now`` is always
passed in.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

# -- versioned contract -------------------------------------------------

QUERY_CONTRACT = "di.mission_sources.query"
QUERY_CONTRACT_VERSION = 1

# -- source kinds (each kind is a reader in mission_sources.sources) ----

KIND_MISSION_CORE = "mission_core"
KIND_WORKFLOW_STORE = "workflow_store"
SOURCE_KINDS = (KIND_MISSION_CORE, KIND_WORKFLOW_STORE)

# -- lifecycle models: identifiers only; literals stay native -----------

LIFECYCLE_MODEL_MISSION_CORE = "mission_core.mission_state/1"
LIFECYCLE_MODEL_WORKFLOW_STORE = "workflow_authority.workflow_phase/2"

# -- membership standing: a gate derived per model, NOT a lifecycle -----

STANDING_ACTIVE = "active"
STANDING_INACTIVE = "inactive"
STANDING_TERMINAL = "terminal"
STANDING_UNKNOWN_FUTURE = "unknown_future"
STANDINGS = (STANDING_ACTIVE, STANDING_INACTIVE, STANDING_TERMINAL,
             STANDING_UNKNOWN_FUTURE)

# -- health of a source or of the runtime snapshot ----------------------

HEALTH_FRESH = "fresh"
HEALTH_STALE = "stale"
HEALTH_UNAVAILABLE = "unavailable"
HEALTH_MALFORMED = "malformed"
HEALTH_INCOMPATIBLE = "incompatible"
HEALTH_STATES = (HEALTH_FRESH, HEALTH_STALE, HEALTH_UNAVAILABLE,
                 HEALTH_MALFORMED, HEALTH_INCOMPATIBLE)

# -- problem codes ------------------------------------------------------

PROBLEM_NONE = None
PROBLEM_SNAPSHOT_ABSENT = "snapshot_absent"
PROBLEM_SNAPSHOT_SHAPE = "snapshot_shape_invalid"
PROBLEM_SNAPSHOT_SCHEMA = "snapshot_schema_unsupported"
PROBLEM_OBSERVED_IN_FUTURE = "observed_at_in_future"
PROBLEM_OBSERVED_TOO_OLD = "observed_at_older_than_bound"
PROBLEM_RESPONSE_SHAPE = "response_shape_invalid"
PROBLEM_RESPONSE_CONTRACT = "response_contract_unsupported"
PROBLEM_REGISTRY_DEGRADED = "response_registry_not_fresh"
PROBLEM_RESPONSE_EXPIRED = "response_expired"

# -- facts that are unknown unless a supported field reports them -------

UNKNOWN = "unknown"
UNREPORTED_FACTS = ("model", "cost", "tokens", "progress")

# -- mission-level binding states ---------------------------------------

BINDING_BOUND = "bound"
BINDING_UNBOUND = "unbound"
BINDING_DISAPPEARED = "disappeared"
BINDING_AMBIGUOUS = "ambiguous"
BINDING_UNVERIFIED = "unverified"
BINDING_NOT_APPLICABLE = "not_applicable"
BINDING_STATES = (BINDING_BOUND, BINDING_UNBOUND, BINDING_DISAPPEARED,
                  BINDING_AMBIGUOUS, BINDING_UNVERIFIED,
                  BINDING_NOT_APPLICABLE)

# -- member classification and activity ---------------------------------

MEMBER_PRESENT = "present"
MEMBER_REPLACED = "replaced"
MEMBER_DISAPPEARED = "disappeared"
MEMBER_AMBIGUOUS = "ambiguous"
MEMBER_CLASSIFICATIONS = (MEMBER_PRESENT, MEMBER_REPLACED,
                          MEMBER_DISAPPEARED, MEMBER_AMBIGUOUS)

# Activity is the agent's own reported status. ``done`` means the agent
# reported its turn finished; it is NEVER mission completion, and
# ``idle`` is never completion either.
ACTIVITY_BUSY = "busy"
ACTIVITY_IDLE = "idle"
ACTIVITY_BLOCKED = "blocked"
ACTIVITY_DONE = "done"
ACTIVITY_UNKNOWN = "unknown"
ACTIVITY_UNKNOWN_FUTURE = "unknown_future"
ACTIVITIES = (ACTIVITY_BUSY, ACTIVITY_IDLE, ACTIVITY_BLOCKED,
              ACTIVITY_DONE, ACTIVITY_UNKNOWN, ACTIVITY_UNKNOWN_FUTURE)
# The runtime's own status literals (exactly the five it reports) and the
# activity each maps to; any other literal is ``unknown_future``.
RUNTIME_STATUS_ACTIVITY = {
    "working": ACTIVITY_BUSY,
    "idle": ACTIVITY_IDLE,
    "blocked": ACTIVITY_BLOCKED,
    "done": ACTIVITY_DONE,
    "unknown": ACTIVITY_UNKNOWN,
}

# -- hard bounds (never derived from input) -----------------------------

DEFAULT_MAX_AGE_SECONDS = 60
MIN_MAX_AGE_SECONDS = 1
MAX_MAX_AGE_SECONDS = 3600
MAX_SNAPSHOT_TASKS = 256
MAX_TASK_BINDINGS = 32
MAX_SNAPSHOT_AGENTS = 512
MAX_SNAPSHOT_TEXT_CHARS = 1024
# Declared polling contract for consumers of the query (exposed in every
# response's ``limits``); the query itself reads no clock and cannot poll.
MIN_POLL_INTERVAL_SECONDS = 5

# -- runtime snapshot shape ---------------------------------------------

SNAPSHOT_SCHEMA = "di.mission_sources.runtime_snapshot"
SNAPSHOT_VERSION = 1
SNAPSHOT_KEYS = ("schema", "version", "observed_at", "producer", "tasks",
                 "agents")
SNAPSHOT_TASK_KEYS = ("task_id", "workspace_realpath", "bindings")
SNAPSHOT_BINDING_KEYS = ("logical", "role", "agent", "session", "pane_id",
                         "workspace_id")
SNAPSHOT_AGENT_KEYS = ("agent", "provider", "status", "session", "pane_id",
                       "workspace_id")


class ContractError(ValueError):
    """A value violates the contract. ``problem`` is a stable code."""

    def __init__(self, problem, message):
        super(ContractError, self).__init__(message)
        self.problem = problem


# -- exact-type validators (refuse, never repair) -----------------------

def require_int(value, location):
    """An exact ``int`` (a ``bool`` is refused) that is not negative."""
    if type(value) is not int or value < 0:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s must be a non-negative int" % location)
    return value


def require_text(value, location, optional=False):
    """An exact non-empty ``str`` within the bound and without NUL; when
    ``optional``, ``None`` is accepted and means "not known"."""
    if value is None and optional:
        return None
    if type(value) is not str or not value or "\x00" in value:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s must be a non-empty string" % location)
    if len(value) > MAX_SNAPSHOT_TEXT_CHARS:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s exceeds %d characters"
                            % (location, MAX_SNAPSHOT_TEXT_CHARS))
    return value


def require_closed_mapping(value, keys, location):
    """A ``dict`` whose key set is exactly ``keys``."""
    if type(value) is not dict:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s must be an object" % location)
    if set(value) != set(keys):
        # repr-sorted: unknown keys may be of mixed, unorderable types.
        missing = sorted(set(keys) - set(value), key=repr)
        extra = sorted((repr(k) for k in set(value) - set(keys)))
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s keys differ (missing %s, unknown %s)"
                            % (location, missing, extra))
    return value


def require_list(value, location, max_items):
    if type(value) is not list:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s must be a list" % location)
    if len(value) > max_items:
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s has %d items; the bound is %d"
                            % (location, len(value), max_items))
    return value


def require_max_age(value, location):
    if type(value) is not int or not (
            MIN_MAX_AGE_SECONDS <= value <= MAX_MAX_AGE_SECONDS):
        raise ContractError(PROBLEM_SNAPSHOT_SHAPE,
                            "%s must be an int in %d..%d seconds"
                            % (location, MIN_MAX_AGE_SECONDS,
                               MAX_MAX_AGE_SECONDS))
    return value


# -- freshness ----------------------------------------------------------

def freshness(observed_at, now, max_age_seconds):
    """Classify one observation time against ``now`` and its bound.

    Returns ``(health, problem)``: ``fresh`` only when
    ``0 <= now - observed_at <= max_age_seconds``; a future time is
    ``malformed`` (a clock inconsistency is never read as recent); an
    older one is ``stale``."""
    require_int(now, "now")
    require_max_age(max_age_seconds, "max_age_seconds")
    if type(observed_at) is not int or observed_at < 0:
        return HEALTH_MALFORMED, PROBLEM_SNAPSHOT_SHAPE
    if observed_at > now:
        return HEALTH_MALFORMED, PROBLEM_OBSERVED_IN_FUTURE
    if now - observed_at > max_age_seconds:
        return HEALTH_STALE, PROBLEM_OBSERVED_TOO_OLD
    return HEALTH_FRESH, PROBLEM_NONE


# -- runtime snapshot values --------------------------------------------

@dataclass(frozen=True)
class SnapshotBinding(object):
    """One role binding recorded for a task (who SHOULD be the agent)."""
    logical: str
    role: Optional[str]
    agent: str
    session: Optional[str]
    pane_id: Optional[str]
    workspace_id: Optional[str]


@dataclass(frozen=True)
class SnapshotTask(object):
    task_id: str
    workspace_realpath: str
    bindings: Tuple[SnapshotBinding, ...]


@dataclass(frozen=True)
class SnapshotAgent(object):
    """One live agent as the runtime reports it (who IS there)."""
    agent: str
    provider: Optional[str]
    status: str
    session: Optional[str]
    pane_id: Optional[str]
    workspace_id: Optional[str]


@dataclass(frozen=True)
class SnapshotReading(object):
    """The validated reading of one injected snapshot value.

    ``tasks`` / ``agents`` are populated only when the value was
    well-formed and compatible (``fresh`` or ``stale``); for the other
    health states they are empty and must not be used."""
    health: str
    problem: Optional[str]
    observed_at: Optional[int]
    max_age_seconds: int
    producer: Optional[str]
    tasks: Tuple[SnapshotTask, ...]
    agents: Tuple[SnapshotAgent, ...]

    def is_live(self):
        return self.health == HEALTH_FRESH

    def describe(self):
        """The JSON-ready health block of a query response."""
        return {
            "health": self.health,
            "problem": self.problem,
            "observed_at": self.observed_at,
            "max_age_seconds": self.max_age_seconds,
            "producer": self.producer,
            "task_count": len(self.tasks),
            "agent_count": len(self.agents),
        }


def _degraded(health, problem, max_age_seconds, observed_at=None,
              producer=None):
    return SnapshotReading(health, problem, observed_at, max_age_seconds,
                           producer, (), ())


def _binding(value, location):
    require_closed_mapping(value, SNAPSHOT_BINDING_KEYS, location)
    return SnapshotBinding(
        logical=require_text(value["logical"], location + ".logical"),
        role=require_text(value["role"], location + ".role", optional=True),
        agent=require_text(value["agent"], location + ".agent"),
        session=require_text(value["session"], location + ".session",
                             optional=True),
        pane_id=require_text(value["pane_id"], location + ".pane_id",
                             optional=True),
        workspace_id=require_text(value["workspace_id"],
                                  location + ".workspace_id", optional=True),
    )


def _task(value, location):
    require_closed_mapping(value, SNAPSHOT_TASK_KEYS, location)
    bindings = require_list(value["bindings"], location + ".bindings",
                            MAX_TASK_BINDINGS)
    return SnapshotTask(
        task_id=require_text(value["task_id"], location + ".task_id"),
        workspace_realpath=require_text(value["workspace_realpath"],
                                        location + ".workspace_realpath"),
        bindings=tuple(_binding(b, "%s.bindings[%d]" % (location, i))
                       for i, b in enumerate(bindings)),
    )


def _agent(value, location):
    require_closed_mapping(value, SNAPSHOT_AGENT_KEYS, location)
    return SnapshotAgent(
        agent=require_text(value["agent"], location + ".agent"),
        provider=require_text(value["provider"], location + ".provider",
                              optional=True),
        status=require_text(value["status"], location + ".status"),
        session=require_text(value["session"], location + ".session",
                             optional=True),
        pane_id=require_text(value["pane_id"], location + ".pane_id",
                             optional=True),
        workspace_id=require_text(value["workspace_id"],
                                  location + ".workspace_id", optional=True),
    )


def read_runtime_snapshot(value, now, max_age_seconds=DEFAULT_MAX_AGE_SECONDS):
    """Validate one injected snapshot value; never raises for bad input.

    ``None`` is ``unavailable``. A value whose ``schema`` / ``version`` is
    not this contract's is ``incompatible`` (checked first, because a
    different version may legitimately have different keys). Any shape
    violation is ``malformed``. A well-formed value is then ``fresh`` or
    ``stale`` by ``freshness``; a future ``observed_at`` is
    ``malformed``."""
    require_int(now, "now")
    require_max_age(max_age_seconds, "max_age_seconds")
    if value is None:
        return _degraded(HEALTH_UNAVAILABLE, PROBLEM_SNAPSHOT_ABSENT,
                         max_age_seconds)
    if type(value) is not dict:
        return _degraded(HEALTH_MALFORMED, PROBLEM_SNAPSHOT_SHAPE,
                         max_age_seconds)
    if value.get("schema") != SNAPSHOT_SCHEMA or type(
            value.get("version")) is not int or value.get(
                "version") != SNAPSHOT_VERSION:
        return _degraded(HEALTH_INCOMPATIBLE, PROBLEM_SNAPSHOT_SCHEMA,
                         max_age_seconds)
    try:
        require_closed_mapping(value, SNAPSHOT_KEYS, "snapshot")
        observed_at = require_int(value["observed_at"], "snapshot.observed_at")
        producer = require_text(value["producer"], "snapshot.producer")
        tasks = tuple(
            _task(t, "snapshot.tasks[%d]" % i) for i, t in enumerate(
                require_list(value["tasks"], "snapshot.tasks",
                             MAX_SNAPSHOT_TASKS)))
        agents = tuple(
            _agent(a, "snapshot.agents[%d]" % i) for i, a in enumerate(
                require_list(value["agents"], "snapshot.agents",
                             MAX_SNAPSHOT_AGENTS)))
    except ContractError as exc:
        return _degraded(HEALTH_MALFORMED, exc.problem, max_age_seconds)
    health, problem = freshness(observed_at, now, max_age_seconds)
    if health == HEALTH_MALFORMED:
        return _degraded(health, problem, max_age_seconds, observed_at,
                         producer)
    return SnapshotReading(health, problem, observed_at, max_age_seconds,
                           producer, tasks, agents)


# -- response envelope and consumer-side freshness ----------------------

RESPONSE_KEYS = ("contract", "contract_version", "generated_at",
                 "max_age_seconds", "valid_until", "limits", "registry",
                 "sources", "runtime_snapshot", "missions",
                 "unmatched_runtime_agent_count")


def envelope(generated_at, max_age_seconds, valid_until, limits, registry,
             sources, runtime_snapshot, missions,
             unmatched_runtime_agent_count):
    """Assemble one query response; the contract identity is always set.

    ``valid_until`` is the instant after which NOTHING in the response may
    be presented as live: the earliest expiry of everything it presents as
    live (see ``mission_sources.query``), never later than ``generated_at +
    max_age_seconds``."""
    require_int(generated_at, "generated_at")
    require_max_age(max_age_seconds, "max_age_seconds")
    require_int(valid_until, "valid_until")
    if not generated_at <= valid_until <= generated_at + max_age_seconds:
        raise ContractError(PROBLEM_RESPONSE_SHAPE,
                            "valid_until must lie within the envelope bound")
    return {
        "contract": QUERY_CONTRACT,
        "contract_version": QUERY_CONTRACT_VERSION,
        "generated_at": generated_at,
        "max_age_seconds": max_age_seconds,
        "valid_until": valid_until,
        "limits": limits,
        "registry": registry,
        "sources": sources,
        "runtime_snapshot": runtime_snapshot,
        "missions": missions,
        "unmatched_runtime_agent_count": unmatched_runtime_agent_count,
    }


def response_freshness(response, now):
    """THE consumer operation: how to treat a response at ``now``.

    Consumer rule (binding on every presentation, e.g. office characters,
    cards, Jarvis tools): call ``response_freshness(response, now)``
    immediately before presenting ANYTHING from a response as live, and
    again at least every ``limits.min_poll_interval_seconds`` while it stays
    on screen. While it returns anything other than ``fresh``, present no
    member, binding or activity as live: remove the characters (or show them
    explicitly as not live) and fetch a new response. Never extend a
    response's life by caching its members.

    Returns ``(health, problem)``: ``incompatible`` for an unknown contract
    or version; ``malformed`` for a wrong shape, a future ``generated_at``,
    or a ``valid_until`` outside the envelope bound; the registry's own
    health when the response was built over a registry that could not be
    loaded; ``stale`` (``response_expired``) once ``now`` is past
    ``valid_until`` — which already accounts for the runtime snapshot's own
    age and every source's registered bound, so a response wrapping an
    almost-expired observation expires with it; else ``fresh``."""
    require_int(now, "now")
    if type(response) is not dict:
        return HEALTH_MALFORMED, PROBLEM_RESPONSE_SHAPE
    if response.get("contract") != QUERY_CONTRACT or type(
            response.get("contract_version")) is not int or response.get(
                "contract_version") != QUERY_CONTRACT_VERSION:
        return HEALTH_INCOMPATIBLE, PROBLEM_RESPONSE_CONTRACT
    if set(response) != set(RESPONSE_KEYS):
        return HEALTH_MALFORMED, PROBLEM_RESPONSE_SHAPE
    max_age = response.get("max_age_seconds")
    if type(max_age) is not int or not (
            MIN_MAX_AGE_SECONDS <= max_age <= MAX_MAX_AGE_SECONDS):
        return HEALTH_MALFORMED, PROBLEM_RESPONSE_SHAPE
    registry = response.get("registry")
    if type(registry) is not dict or registry.get("health") not in HEALTH_STATES:
        return HEALTH_MALFORMED, PROBLEM_RESPONSE_SHAPE
    if registry["health"] != HEALTH_FRESH:
        return registry["health"], PROBLEM_REGISTRY_DEGRADED
    generated_at = response.get("generated_at")
    valid_until = response.get("valid_until")
    if type(generated_at) is not int or type(valid_until) is not int or \
            not generated_at <= valid_until <= generated_at + max_age:
        return HEALTH_MALFORMED, PROBLEM_RESPONSE_SHAPE
    if generated_at > now:
        return HEALTH_MALFORMED, PROBLEM_OBSERVED_IN_FUTURE
    if now > valid_until:
        return HEALTH_STALE, PROBLEM_RESPONSE_EXPIRED
    return HEALTH_FRESH, PROBLEM_NONE
