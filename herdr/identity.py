"""Logical role identity versus transient agent/session identity.

WHY THIS EXISTS
---------------
A Herdr role is a LOGICAL name that outlives the process behind it.
The thing Herdr hands back is a TRANSIENT agent record whose session
id, revision and terminal change under `/clear`, a server restart, a
session replacement, and a model substitution. Before this module the
two were compared only by name, and every divergence collapsed into
one refusal — "Refusing to clear `x` while status is `missing`" —
with no rediscovery and no clean rebootstrap.

This module separates the cases that refusal conflated, gives each its
own problem code and its own deterministic action, and decides them
from evidence rather than from a guess.

THE FIELD SPLIT, DERIVED FROM THE DEPENDENCY'S OWN OUTPUT
---------------------------------------------------------
`herdr agent list` returns records carrying, in the observed
production shape:

- STABLE (survives a session being replaced): ``name``, ``cwd``,
  ``workspace_id``, ``pane_id``.
- TRANSIENT (replaced with the session): ``agent_session`` (whose
  ``value`` is the session id), ``revision``, ``state_change_seq``,
  ``terminal_id``, ``agent_status``, ``interactive_ready``.

`tests/test_identity.py` pins both sets against the REAL binary's own
output, so a schema move fails there rather than here.

WHAT THIS MODULE DECIDES, AND WHAT IT REFUSES TO DECIDE
-------------------------------------------------------
Four verdicts, each with its own code and action:

- ``PRESENT`` — stable identity matches and the session is the one
  last bound. Proceed.
- ``REPLACED`` — stable identity matches, the session id does not.
  The logical role survived; its process did not. Clean rebootstrap:
  re-seed the role contract into the new session and rebind.
- ``MISSING`` — Herdr has no record under that name. Rediscovery is
  attempted from the live listing, and ONLY exact evidence is
  accepted. Within this module a MISSING target receives no command
  at all: it is not prompted, cleared or re-seeded. Outside this
  module, and disclosed: another caller holding the same name is not
  constrained by this.
- ``DEGRADED`` — Herdr answered in a shape this module does not
  understand. That is an absence of evidence, not evidence of health,
  so it BLOCKS.

Rediscovery accepts a candidate when exactly one live agent matches
the binding's stable identity. Zero candidates, more than one, or a
candidate whose other stable fields disagree each produce a durable
BLOCK with its own code, so within this function a lookalike is not
adopted silently.

Scope of that guarantee: the decision is made from the listing passed
in. Outside it, and disclosed: a stale listing yields a stale answer,
and this module has no way to distinguish one from a fresh one,
because `herdr agent list` carries no generation marker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Fields that survive the session being replaced.
STABLE_FIELDS = ("name", "cwd", "workspace_id", "pane_id")

#: Fields replaced along with the session.
TRANSIENT_FIELDS = (
    "agent_session",
    "revision",
    "state_change_seq",
    "terminal_id",
    "agent_status",
    "interactive_ready",
)

#: The stable fields rediscovery matches on. `name` is deliberately
#: excluded: a MISSING verdict means the name no longer resolves, so
#: matching on it could not succeed by construction.
REDISCOVERY_FIELDS = ("workspace_id", "pane_id")

VERDICT_PRESENT = "present"
VERDICT_REPLACED = "replaced"
VERDICT_MISSING = "missing"
VERDICT_DEGRADED = "degraded"
#: R-53 AQ-3: a live agent absent from this herd's own records.
#:
#: It used to be PRESENT. That was the fail-open half of I2: with no
#: binding on disk the lookalike guard iterated an empty dict and the
#: session-replacement guard tested a None, so both fell through to
#: PRESENT and an unbound role reported healthy. PRESENT means "the
#: stable identity matches and the session is the one last bound" —
#: with no binding recorded there is no such session, and saying
#: PRESENT asserts a comparison that did not happen.
VERDICT_UNBOUND = "unbound"

VERDICTS = (
    VERDICT_PRESENT,
    VERDICT_REPLACED,
    VERDICT_MISSING,
    VERDICT_DEGRADED,
    VERDICT_UNBOUND,
)

ACTION_PROCEED = "proceed"
ACTION_REBOOTSTRAP = "rebootstrap"
ACTION_REDISCOVER = "rediscover"
ACTION_BLOCK = "block"
#: What an UNBOUND role needs: a binding established from exact live
#: evidence. Its own action rather than ACTION_BLOCK reused, because
#: the remedy is different: the agent is healthy, and what is absent
#: is this herd's record of it.
ACTION_BIND = "bind"

ACTIONS = (
    ACTION_PROCEED,
    ACTION_REBOOTSTRAP,
    ACTION_REDISCOVER,
    ACTION_BLOCK,
    ACTION_BIND,
)

PROBLEM_AGENT_MISSING = "identity_agent_missing"
PROBLEM_AGENT_DEGRADED = "identity_agent_probe_degraded"
PROBLEM_SESSION_REPLACED = "identity_session_replaced"
PROBLEM_STABLE_MISMATCH = "identity_stable_identity_mismatch"
PROBLEM_NO_CANDIDATE = "identity_rediscovery_no_candidate"
PROBLEM_AMBIGUOUS = "identity_rediscovery_ambiguous"
PROBLEM_CANDIDATE_CONFLICT = "identity_rediscovery_candidate_conflict"
PROBLEM_BINDING_INCOMPLETE = "identity_binding_incomplete"
PROBLEM_ROLE_UNBOUND = "identity_role_unbound"
PROBLEM_BINDINGS_CORRUPT = "identity_bindings_corrupt"

#: Every code this module can produce. Consumers reference this rather
#: than retyping a literal.
IDENTITY_PROBLEM_CODES = (
    PROBLEM_AGENT_MISSING,
    PROBLEM_AGENT_DEGRADED,
    PROBLEM_SESSION_REPLACED,
    PROBLEM_STABLE_MISMATCH,
    PROBLEM_NO_CANDIDATE,
    PROBLEM_AMBIGUOUS,
    PROBLEM_CANDIDATE_CONFLICT,
    PROBLEM_BINDING_INCOMPLETE,
    PROBLEM_ROLE_UNBOUND,
    PROBLEM_BINDINGS_CORRUPT,
)

#: The statuses a context reset may proceed against. Derived from the
#: real `agent_status` domain (`blocked`, `done`, `idle`, `working`).
RESETTABLE_STATUSES = ("idle", "done")

#: Values `agent_info` SYNTHESISES when its probe fails. Herdr itself
#: does not emit either as an `agent_status`, which
#: `tests/test_identity.py` pins against the real binary's output.
#: They are separated from the real domain because a probe failure is
#: an identity question — MISSING or DEGRADED — and not a busy agent.
PROBE_SENTINELS = ("missing", "unknown")


def is_busy(status):
    """Is this a REAL Herdr status that forbids a context reset?

    True only for a status Herdr itself reported that is outside
    `RESETTABLE_STATUSES`. A probe sentinel returns False here, so it
    routes to classification rather than to the busy refusal — the
    conflation this increment exists to undo.
    """
    if status in PROBE_SENTINELS:
        return False
    return status not in RESETTABLE_STATUSES


BINDINGS_FILE = "role-bindings.json"
BINDINGS_VERSION = 1

#: What a binding must carry before this module will compare anything
#: against it. A binding missing one of these is unable to answer the
#: question PRESENT claims to have answered.
BINDING_REQUIRED_FIELDS = ("logical", "agent", "session")


class BindingsCorrupt(Exception):
    """The bindings document exists and is not trustworthy.

    A REFUSAL, within this reader never a silent rebuild (R-53 AQ-5).
    The previous reader mapped every failure — unparsable, wrong-shaped, unreadable — onto
    an empty document, so a corrupt file was indistinguishable from a
    first run and the next save overwrote it. Overwriting is the worst
    available response: the file being unreadable is evidence that
    something is wrong, and rebuilding it destroys that evidence while
    reporting success.
    """


def binding_gap(binding):
    """Why ``binding`` is, within this module, unusable for
    comparison — or None.

    The SINGLE definition of "bound". `classify` and the `Verdict`
    constructor both consult it, so within it they cannot disagree
    about what UNBOUND means.
    """
    if not isinstance(binding, dict) or not binding:
        return "no binding is recorded for this role"
    missing = [
        field for field in BINDING_REQUIRED_FIELDS
        if not isinstance(binding.get(field), str)
        or not binding.get(field)
    ]
    if missing:
        return "the recorded binding carries no %s" % ", ".join(missing)
    stable = binding.get("stable")
    if not isinstance(stable, dict) or not stable:
        return "the recorded binding carries no stable identity"
    return None


class Verdict:
    """One role's identity decision: what was observed, what it means,
    and the single action that follows from it.

    A PRESENT verdict is UNCONSTRUCTIBLE WITHOUT THE SESSION IT WAS
    CONFIRMED AGAINST (R-53 AQ-3). That is the construction half, and
    it is the same standard that removed `close_fn`'s default and
    `required_names`' empty tuple: the wrong state has to be
    UNWRITABLE, not merely wrong. PRESENT asserts "the session is the
    one last bound"; a caller unable to name that session has not
    made the comparison, and here it is unable to claim otherwise —
    the constructor raises, in this module and in every caller and
    test alike.
    """

    __slots__ = ("logical", "verdict", "action", "problem", "detail",
                 "observed", "bound_session")

    def __init__(self, logical, verdict, action, problem=None,
                 detail=None, observed=None, bound_session=None):
        if verdict == VERDICT_PRESENT and not bound_session:
            raise ValueError(
                "a PRESENT verdict for %r requires the bound session"
                " it was confirmed against; a live role this herd has"
                " never bound is UNBOUND, not PRESENT (R-53 AQ-3)"
                % (logical,)
            )
        self.bound_session = bound_session
        self.logical = logical
        self.verdict = verdict
        self.action = action
        self.problem = problem
        self.detail = detail
        self.observed = observed

    def as_dict(self):
        return {
            "logical": self.logical,
            "verdict": self.verdict,
            "action": self.action,
            "problem": self.problem,
            "detail": self.detail,
        }

    def __repr__(self):                     # pragma: no cover
        return "Verdict(%s, %s, %s, %s)" % (
            self.logical, self.verdict, self.action, self.problem,
        )


def agent_record(info):
    """The agent record inside an ``agent_info`` result, or None.

    `agent_info` returns ``{"status": ..., "raw": <parsed JSON>}``.
    The real envelope is ``{"id", "result": {"agent": {...}}}``. A raw
    payload shaped otherwise yields None, which the caller reads as a
    degraded probe rather than as an absent agent.
    """
    if not isinstance(info, dict):
        return None
    raw = info.get("raw")
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if not isinstance(result, dict):
        return None
    record = result.get("agent")
    return record if isinstance(record, dict) else None


def session_value(record):
    """The transient session id, or None when the record omits it."""
    if not isinstance(record, dict):
        return None
    session = record.get("agent_session")
    if not isinstance(session, dict):
        return None
    value = session.get("value")
    return value if isinstance(value, str) and value else None


def stable_identity(record):
    """The subset of a record that survives session replacement."""
    if not isinstance(record, dict):
        return {}
    return {
        field: record.get(field)
        for field in STABLE_FIELDS
        if isinstance(record.get(field), str)
    }


def binding_for(logical, agent, record):
    """A durable binding: the logical role, the agent name it resolves
    to, its stable identity, and the session last seen holding it."""
    binding = {
        "logical": logical,
        "agent": agent,
        "stable": stable_identity(record),
        "session": session_value(record),
    }
    return binding


def bindings_path(herd_root):
    return Path(herd_root) / "state" / BINDINGS_FILE


def load_bindings(herd_root):
    """Durable role bindings. EACH FAILURE DECIDED SEPARATELY.

    R-53 AQ-5 asked for a decision per case rather than one fallback
    covering all of them, and the cases are genuinely different:

    ABSENT -> an EMPTY DOCUMENT. Within this reader an absent file is a herd that has not bound yet. Within this reader that is the ordinary first-run state
    rather than a fault. It is the one case returning empty.

    UNREADABLE (a permissions or I/O error) -> REFUSAL. The file may
    exist and say something; not being able to read it is not the
    same as it not being there, and treating it as absent would let a
    save overwrite bindings this process could not see.

    MALFORMED (present, does not parse) -> REFUSAL.

    WRONG-SHAPED (parses, is not a bindings document) -> REFUSAL.

    The last three are CORRUPT, and within this reader corrupt is
    never silently rebuilt.
    The previous reader mapped all four onto an empty document, which
    made a corrupt file indistinguishable from a first run — and the
    next save then overwrote it, destroying the only evidence that
    anything was wrong while reporting success.
    """
    path = bindings_path(herd_root)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {"version": BINDINGS_VERSION, "roles": {}}
    except OSError as exc:
        raise BindingsCorrupt(
            "the bindings document at %s could not be read (%s); an"
            " unreadable file is not an absent one, and rebuilding it"
            " would overwrite bindings this process cannot see"
            % (path, exc)
        )
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise BindingsCorrupt(
            "the bindings document at %s does not parse (%s); it is"
            " REFUSED rather than rebuilt, because rebuilding"
            " destroys the evidence that something wrote it wrong"
            % (path, exc)
        )
    if not isinstance(document, dict):
        raise BindingsCorrupt(
            "the bindings document at %s is a %s, not a document"
            % (path, type(document).__name__)
        )
    roles = document.get("roles")
    if not isinstance(roles, dict):
        raise BindingsCorrupt(
            "the bindings document at %s carries no `roles` mapping"
            % (path,)
        )
    return {"version": BINDINGS_VERSION, "roles": roles}


def save_bindings(herd_root, document):
    """Write the bindings ATOMICALLY (R-53 AQ-5).

    K-1's discipline, and the reason it applies here: a partially
    written binding is WORSE than no binding, because it reads as
    authoritative. A truncated document that still parses names a
    subset of roles, and every role it omits then classifies as
    UNBOUND while the file itself looks intact.

    So the bytes land in a temp file in the SAME directory, are
    fsynced, and are moved into place with `os.replace`, which is
    atomic on this platform. A crash leaves the previous document, or
    none, and within this write never a half-written one. The directory
    is fsynced after, so the rename itself survives.
    """
    path = bindings_path(herd_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(
        path.name + ".%d.partial" % os.getpid()
    )
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))
    directory = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return path


def classify(logical, binding, info):
    """Decide one role's identity from its binding and a fresh probe.

    PURE. It reads a binding and a probe and returns a verdict.
    Within it, writing and binding are both absent.

    R-53 AQ-3: the docstring that stood here said an unbound live
    agent "reads as PRESENT and is bound". Both halves were wrong.
    This function is pure, so within it no binding could happen, and
    no caller bound on its behalf either — a pure function is unable to
    promise a side effect. And reading such a role as PRESENT was the
    fail-open defect itself: with no binding the lookalike guard
    iterated an empty mapping and the session guard tested a None, so
    both fell through and an unbound role reported healthy.

    An unbound or incomplete binding is now VERDICT_UNBOUND with
    ACTION_BIND. Establishing the binding is the BOOTSTRAP's job
    (`lifecycle.establish_role_bindings`), which does it from exact
    live evidence before the herd is reported ready.
    """
    status = info.get("status") if isinstance(info, dict) else None
    record = agent_record(info)

    if status == "missing" and record is None:
        return Verdict(
            logical, VERDICT_MISSING, ACTION_REDISCOVER,
            PROBLEM_AGENT_MISSING,
            "Herdr returned no record for `%s`" % (
                binding.get("agent") if binding else logical
            ),
        )

    if record is None:
        return Verdict(
            logical, VERDICT_DEGRADED, ACTION_BLOCK,
            PROBLEM_AGENT_DEGRADED,
            "the probe for `%s` returned status %r with no readable"
            " agent record; absence of evidence is not health" % (
                (binding.get("agent") if binding else logical), status,
            ),
        )

    # THE UNBOUND CHECK SITS HERE, after the probe questions and
    # before every comparison that reads the binding. Ordering is the
    # point: the two guards below both consult `binding`, and within
    # that state they compared nothing and fell through.
    gap = binding_gap(binding)
    if gap is not None:
        return Verdict(
            logical, VERDICT_UNBOUND, ACTION_BIND,
            PROBLEM_ROLE_UNBOUND,
            "`%s` is live and this herd has never bound it: %s. A"
            " role with no recorded session cannot be compared"
            " against one, so it is UNBOUND rather than PRESENT"
            % (logical, gap),
            stable_identity(record),
        )

    observed = stable_identity(record)
    recorded = (binding or {}).get("stable") or {}
    conflicting = sorted(
        field for field, value in recorded.items()
        if observed.get(field) != value
    )
    if conflicting:
        return Verdict(
            logical, VERDICT_DEGRADED, ACTION_BLOCK,
            PROBLEM_STABLE_MISMATCH,
            "stable identity for `%s` disagrees on %s; a lookalike is"
            " not adopted" % (logical, ", ".join(conflicting)),
            observed,
        )

    bound_session = binding["session"]
    live_session = session_value(record)
    if not live_session:
        # The binding names a session and the live record does not.
        # That is an unanswerable comparison, not a match: absence of
        # evidence is not health, which is the same rule DEGRADED
        # already encodes for an unreadable probe.
        return Verdict(
            logical, VERDICT_DEGRADED, ACTION_BLOCK,
            PROBLEM_AGENT_DEGRADED,
            "`%s` is bound to session %s and the live record carries"
            " no session id, so the comparison PRESENT asserts cannot"
            " be made" % (logical, bound_session),
            observed,
        )
    if bound_session != live_session:
        return Verdict(
            logical, VERDICT_REPLACED, ACTION_REBOOTSTRAP,
            PROBLEM_SESSION_REPLACED,
            "`%s` is held by session %s, not the bound %s; the"
            " logical role survived and its contract is re-seeded"
            % (logical, live_session, bound_session),
            observed,
        )

    return Verdict(logical, VERDICT_PRESENT, ACTION_PROCEED,
                   None, None, observed,
                   bound_session=bound_session)


def listed_agents(listing):
    """The agent records inside a `herdr agent list` payload.

    Scope: the observed envelope ``{"result": {"agents": [...]}}``.
    Outside it, and disclosed: a payload shaped otherwise yields an
    empty list, which rediscovery reads as "no candidate" and blocks
    on — it does not read as a successful empty listing.
    """
    if not isinstance(listing, dict):
        return []
    result = listing.get("result")
    if not isinstance(result, dict):
        return []
    agents = result.get("agents")
    if not isinstance(agents, list):
        return []
    return [record for record in agents if isinstance(record, dict)]


def rediscover(logical, binding, listing):
    """Find the agent now holding a logical role, on exact evidence.

    Returns ``(record, problem, detail)``. A candidate is accepted
    when exactly one live agent matches every field in
    ``REDISCOVERY_FIELDS`` and disagrees with none of the binding's
    other stable fields. Zero, several, or a conflicting candidate
    each return their own problem code and no record.
    """
    recorded = (binding or {}).get("stable") or {}
    wanted = {
        field: recorded.get(field)
        for field in REDISCOVERY_FIELDS
        if isinstance(recorded.get(field), str)
    }
    if len(wanted) != len(REDISCOVERY_FIELDS):
        return None, PROBLEM_BINDING_INCOMPLETE, (
            "the binding for `%s` records %s of the %d fields"
            " rediscovery matches on, so no exact match is possible"
            % (logical, sorted(wanted) or "none",
               len(REDISCOVERY_FIELDS))
        )

    candidates = [
        record for record in listed_agents(listing)
        if all(record.get(field) == value
               for field, value in wanted.items())
    ]
    if not candidates:
        return None, PROBLEM_NO_CANDIDATE, (
            "no live agent matches %s for `%s`" % (wanted, logical)
        )
    if len(candidates) > 1:
        return None, PROBLEM_AMBIGUOUS, (
            "%d live agents match %s for `%s`; an ambiguous match is"
            " never adopted" % (len(candidates), wanted, logical)
        )

    candidate = candidates[0]
    observed = stable_identity(candidate)
    # `name` is excluded for the same reason it is excluded from
    # REDISCOVERY_FIELDS: a MISSING verdict means the recorded name no
    # longer resolves, so the agent now holding the role is expected
    # to carry a different one. Comparing it would reject every real
    # rediscovery. Every OTHER stable field must still agree.
    conflicting = sorted(
        field for field, value in recorded.items()
        if field not in wanted
        and field != "name"
        and observed.get(field) != value
    )
    if conflicting:
        return None, PROBLEM_CANDIDATE_CONFLICT, (
            "the single candidate for `%s` disagrees on %s"
            % (logical, ", ".join(conflicting))
        )
    return candidate, None, None
