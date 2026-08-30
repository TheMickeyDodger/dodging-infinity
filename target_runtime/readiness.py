"""I3: the target Herdr's BOOTSTRAP READINESS lifecycle.

The problem this module exists for
==================================

After a dispatch succeeds, ``Broker._verify`` polls the target and, for
as long as the target has not stopped, returns ``target_running`` and
waits. That wait is CORRECT for engineering — within this increment no
deadline is added to a mission anywhere — but until now the same wait
also covered a target that had not become usable: a role that failed to
register, or a Herdr sitting at an interactive startup prompt an
unattended run is unable to answer. Those are BOOTSTRAP FAILURES rather
than productive engineering, and a system with nobody watching lacks
no way, within the evidence it already has, to tell them
apart from a long compile.

So: **bootstrap gets a bound; engineering does not.** The bound applies
only within the window between dispatch and the FIRST positively
evidenced readiness, and it stops permanently once readiness has been
evidenced once. Outside that window this module returns
``BOOTSTRAP_READY`` from durable state alone: within it there is no
probe, no clock read, and no bound.

Why a separate evidence path, and not the production observation
================================================================

``target_runtime/broker.py`` observes with ``probe_agents=False``, and
``herdr/observe.py::_agents_section`` fills ``listed`` with
``probe: "unprobed"`` in that mode, so in production the ``agents``
section carries no live agent facts by construction. Two consequences
were derived by execution before this module was designed:

1. Consuming ``agents`` in a registered consumed-source set would
   reproduce the defect ``evidence.py`` already records by name: a
   production observation is globally PARTIAL whenever agents are
   unprobed, and a gate that consumed it would put every production
   workflow permanently in the blocked branch.
2. Flipping ``probe_agents`` to true would not be sufficient anyway.
   Within ``_probe_one`` only the allowlisted status string is
   projected, and its own docstring states the bound: the raw payload
   is outside what it emits. So ``interactive_ready`` does not survive
   that projection, and readiness through the observer would need two
   changes — a probing mode AND a wider projection — the second of
   which loosens a deliberate allowlist on a path that feeds a model.

A separate, explicitly bounded, read-only probe used only within the
bootstrap window is the smaller change and the one whose blast radius
can be stated: it adds a source rather than widening one, it leaves
every existing advance/block decision reading exactly the diagnostics
it read before, and its scope: the pre-readiness window of one
DISPATCHED workflow.

Which way this signal fails
===========================

**It fails toward NOT READY.** A probe that is unable to see the target
does not pass silently: within this layer that records
``BOOTSTRAP_UNOBSERVABLE``, distinct from ``BOOTSTRAP_FAILED``, so a
human reading the durable state can tell "we could not look" from "we
looked and it was not ready".

But it does not fail all the way to a STOP on absence alone. Only a
POSITIVELY OBSERVED not-ready target — a probe that answered, and whose
answer was missing a role or showed one not interactive — can exhaust
the bound and block. Bounding on absence would expire a mission that is
running fine and merely unreadable, which is a deadline on engineering
in bootstrap clothing; an existing guarantee test caught exactly that
when an earlier draft of this module stopped on unobservable.

The residual that leaves, stated in the same breath as the guarantee: a
target that is BOTH stuck at a startup prompt AND unobservable waits
without a bound, and within this layer no readable signal separates it
from a healthy unobservable target. That wait is recorded durably from
its first occurrence, so it is disclosed rather than silent.

Staleness
=========

A status that is true but STALE reads exactly like one that is true and
CURRENT, unless something monotonic is checked alongside it. The live
agent vocabulary carries two monotonic counters per agent, ``revision``
and ``state_change_seq``. This module records the pair it saw with each
readiness verdict and REFUSES to inherit a prior READY verdict for an
agent whose pair has gone BACKWARD, since a counter that decreased
means the name is now held by a different process rather than that the
same agent got younger. Outside that: a counter that stands still is
not treated as regression, so a stale-but-equal reading is not caught
here.

Durable shape
=============

No record schema change. Readiness is evidenced by ``evidence``
receipts carrying a fixed marker — the shape
``workspace_trust.trust_block_receipt`` already established — and the
verdict is DERIVED from those receipts, the phase, and
``target_engine["dispatched_at"]``. That mirrors
``runtime.dispatch_identity_unresolved``, which derives from
``target_engine`` rather than storing a new field.

Within this module ``ambiguity`` is neither read nor written (a
non-none state there makes a workflow unclaimable), and
``target_engine`` is neither read for a readiness decision nor written
by one (its ``None`` IS the dispatch-before-spawn crash boundary). So
that crash boundary is preserved as it stands, and its scope: unchanged
and not widened by this increment.
"""

import hashlib
import secrets

BOOTSTRAP_RECEIPT_MARKER = "target bootstrap readiness"

#: The four verdicts. Within this lifecycle `READY` is terminal:
#: once evidenced, re-derivation from a probe is outside what
#: `evaluate` does.
BOOTSTRAP_WAITING = "bootstrap_waiting"
BOOTSTRAP_READY = "bootstrap_ready"
BOOTSTRAP_UNOBSERVABLE = "bootstrap_unobservable"
BOOTSTRAP_FAILED = "bootstrap_failed"

BOOTSTRAP_STATES = (
    BOOTSTRAP_WAITING,
    BOOTSTRAP_READY,
    BOOTSTRAP_UNOBSERVABLE,
    BOOTSTRAP_FAILED,
)

#: The one problem code this layer can stop with. There is exactly one
#: because there is exactly one stopping state: a POSITIVELY OBSERVED
#: not-ready target whose bootstrap window is exhausted. An earlier
#: draft carried a second code for the unobservable stop; that stop was
#: removed when it turned out to be a mission deadline in disguise, and
#: the code went with it rather than staying as an unreachable name.
PROBLEM_BOOTSTRAP_INCOMPLETE = "target_bootstrap_incomplete"

#: The bootstrap bound, in seconds. It bounds the window from the
#: durable dispatch timestamp to the first evidenced readiness, and it
#: bounds nothing outside that window; an engineering mission is
#: not subject to it,
#: because `evaluate` returns from durable state before consulting a
#: clock once readiness has been evidenced.
BOOTSTRAP_MAX_SECONDS = 900

#: The logical roles a target herd must register before it is ready.
#: Held as a closed set: readiness is POSITIVELY evidenced, so a probe
#: that reports three of four roles is not ready, and a probe that
#: reports roles this set does not name does not substitute for a
#: missing one.
REQUIRED_LOGICAL_ROLES = ("executor1", "lead1", "reviewer1", "supervisor")

#: The live agent fields this module consumes, pinned here so the
#: contract test can assert the double accepts no more than the real
#: dependency emits.
CONSUMED_AGENT_FIELDS = (
    "agent_status",
    "interactive_ready",
    "name",
    "revision",
    "state_change_seq",
)

#: `agent_status` values that are compatible with a bootstrapped agent.
#: Derived from the live vocabulary observed on `herdr agent list`
#: (`blocked`, `done`, `idle`, `working`); all four are states a
#: registered agent reaches AFTER it is interactive, so readiness does
#: not turn on which of them is current.
READY_AGENT_STATUSES = ("blocked", "done", "idle", "working")


class ReadinessError(Exception):
    """A malformed readiness probe result."""


def _monotonic_pair(record):
    """``(revision, state_change_seq)`` as ints, or None.

    Returns None rather than a default when either counter is absent
    or not an integer, because a fabricated zero would compare as "not
    gone backward" and defeat the staleness check this pair exists for.
    """
    revision = record.get("revision")
    sequence = record.get("state_change_seq")
    for value in (revision, sequence):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    return (revision, sequence)


def role_is_ready(record):
    """Whether ONE probed role record evidences a bootstrapped agent.

    Positive evidence, every clause required: the record is a mapping,
    it is registered under a non-empty name, ``interactive_ready`` is
    exactly ``True`` (not merely truthy — a string or a 1 is a
    different dependency than the one this was derived against), its
    ``agent_status`` is one of the observed live vocabulary, and both
    monotonic counters are present as integers.
    """
    if not isinstance(record, dict):
        return False
    name = record.get("name")
    if not isinstance(name, str) or not name:
        return False
    if record.get("interactive_ready") is not True:
        return False
    if record.get("agent_status") not in READY_AGENT_STATUSES:
        return False
    return _monotonic_pair(record) is not None


def probe_verdict(probe_result, prior_pairs=None):
    """The readiness verdict for ONE probe result.

    ``probe_result`` is the injected seam's return: either None /
    a non-mapping (the probe could not see the target) or a mapping of
    logical role name to that role's agent record.

    Returns ``(state, detail, pairs)``. ``pairs`` maps each role whose
    record carried both counters to the pair observed, so the caller
    can persist it and a later call can refuse to inherit a READY
    verdict across a counter that went backward.
    """
    if not isinstance(probe_result, dict):
        return (
            BOOTSTRAP_UNOBSERVABLE,
            "the readiness probe returned no agent mapping; the target"
            " may be healthy and unseen, so this is recorded as"
            " unobservable rather than as a failure",
            {},
        )
    pairs = {}
    missing = []
    not_ready = []
    for logical in REQUIRED_LOGICAL_ROLES:
        record = probe_result.get(logical)
        if record is None:
            missing.append(logical)
            continue
        pair = _monotonic_pair(record) if isinstance(record, dict) else None
        if pair is not None:
            pairs[logical] = pair
        if not role_is_ready(record):
            not_ready.append(logical)
    regressed = _regressed_roles(pairs, prior_pairs)
    if regressed:
        return (
            BOOTSTRAP_WAITING,
            "monotonic counters went BACKWARD for %s, so the name is"
            " held by a different process than the one previously"
            " observed; readiness is re-derived rather than inherited"
            % ", ".join(sorted(regressed)),
            pairs,
        )
    if missing:
        return (
            BOOTSTRAP_WAITING,
            "logical role(s) not registered: %s"
            % ", ".join(sorted(missing)),
            pairs,
        )
    if not_ready:
        return (
            BOOTSTRAP_WAITING,
            "logical role(s) registered but not interactive-ready: %s"
            % ", ".join(sorted(not_ready)),
            pairs,
        )
    return (
        BOOTSTRAP_READY,
        "all %d logical roles registered and interactive-ready"
        % len(REQUIRED_LOGICAL_ROLES),
        pairs,
    )


def _regressed_roles(pairs, prior_pairs):
    """Roles whose monotonic pair is strictly LOWER than the pair
    recorded for the same role earlier. Within this helper an absent
    prior, or an absent current, is skipped; it reports observed
    regression, and inferring one from a gap is outside its scope."""
    if not isinstance(prior_pairs, dict):
        return []
    found = []
    for logical, pair in pairs.items():
        prior = prior_pairs.get(logical)
        if not isinstance(prior, (list, tuple)) or len(prior) != 2:
            continue
        try:
            if tuple(pair) < tuple(prior):
                found.append(logical)
        except TypeError:
            continue
    return found


def readiness_receipt(state, detail, now, turn_id_factory=None):
    """A durable, actionable readiness receipt.

    Same shape as the trust-block receipt this file follows: kind
    ``evidence``, a fixed marker, and a digest over the state string,
    so the receipt is self-describing and carries no capability, no
    path, and no raw probe payload.
    """
    if state not in BOOTSTRAP_STATES:
        raise ReadinessError(
            "readiness state %r is outside the closed domain %r"
            % (state, BOOTSTRAP_STATES)
        )
    make_turn_id = turn_id_factory or (
        lambda: "bready-" + secrets.token_hex(8)
    )
    summary = "%s: %s" % (BOOTSTRAP_RECEIPT_MARKER, state)
    if detail:
        summary = "%s (%s)" % (summary, detail)
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(state.encode("utf-8")).hexdigest(),
        "bounded_summary": summary[:400],
    }


def recorded_states(entry):
    """Every readiness state this record durably carries, oldest
    first, read from the receipts already on the entry."""
    found = []
    for receipt in entry.get("receipts") or []:
        if not isinstance(receipt, dict):
            continue
        summary = receipt.get("bounded_summary")
        if not isinstance(summary, str):
            continue
        if not summary.startswith(BOOTSTRAP_RECEIPT_MARKER + ": "):
            continue
        rest = summary[len(BOOTSTRAP_RECEIPT_MARKER) + 2:]
        state = rest.split(" ", 1)[0].strip()
        if state in BOOTSTRAP_STATES:
            found.append(state)
    return found


def readiness_evidenced(entry):
    """Whether this workflow has EVER durably evidenced readiness.

    THE LOAD-BEARING PREDICATE for the hard line. When it is true the
    caller does not probe, does not consult a clock, and does not
    bound anything — an engineering mission that has once been ready
    runs without a deadline from this module, however long it takes.
    Derived from durable receipts, so within this record it survives
    a restart and a later probe failure does not un-evidence it;
    outside that, a caller that rewrites the receipts rewrites this.
    """
    return BOOTSTRAP_READY in recorded_states(entry)


def last_recorded_state(entry):
    """The most recent readiness state on the record, or None. Used to
    write a receipt only when the state CHANGES, so a target polled
    repeatedly does not churn the store."""
    states = recorded_states(entry)
    return states[-1] if states else None


def bootstrap_deadline_passed(entry, now, max_seconds=None):
    """Whether the bootstrap bound is exhausted.

    Measured from ``target_engine["dispatched_at"]`` — DURABLE state,
    rather than a process start time — so within this function a
    Runtime restart neither restarts the bound nor replays it. Returns False when the timestamp is
    absent or unusable, because within this function a bound it is
    unable to compute must not become a block it is unable to
    justify; the residual that leaves is the unbounded-wait case,
    which the caller still surfaces through the WAITING receipt.
    """
    bound = BOOTSTRAP_MAX_SECONDS if max_seconds is None else max_seconds
    engine = entry.get("target_engine")
    if not isinstance(engine, dict):
        return False
    started = _seconds(engine.get("dispatched_at"))
    current = _seconds(now)
    if started is None or current is None:
        return False
    return (current - started) > bound


def _seconds(value):
    """One record timestamp as seconds, or None.

    The domain is the record's own, not a guess: ``record.py``'s
    ``_require_timestamp`` accepts a non-negative int or float and
    REFUSES a bool, so within this store timestamps are numbers and
    ISO-8601 text is outside the domain. An earlier draft of this
    function parsed ISO strings and so found no deadline at all;
    within the bootstrap window the bound was unreachable, silently,
    until execution caught it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return float(value)


def evaluate(entry, probe_fn, now, max_seconds=None, prior_pairs=None):
    """The bootstrap-readiness decision for one DISPATCHED workflow.

    Returns ``(state, detail, pairs, probed, stop)``. ``stop`` is the
    caller's instruction and is returned rather than re-derived,
    because whether a state stops the workflow depends on the bound
    and not on the state alone: ``BOOTSTRAP_UNOBSERVABLE`` inside the
    window is a wait that has been RECORDED, and the same state with
    the window exhausted is a durable stop.

    Ordering is load-bearing. Durable evidence is consulted FIRST, so a
    workflow that has once been ready returns ``BOOTSTRAP_READY``
    without a probe, without a clock read, and without a bound — the
    property this increment exists to provide. Only a workflow still
    inside its bootstrap window is probed at all.

    A probe that raises is caught and reported as UNOBSERVABLE rather
    than propagating, because within this call an exception from an
    injected seam is a fact about the probe and not about the target;
    outside this call, a probe that raises consistently for the whole
    bootstrap window still ends in a durable block, which is the
    residual named in the module docstring.
    """
    if readiness_evidenced(entry):
        return (
            BOOTSTRAP_READY,
            "readiness was evidenced durably; this workflow is past"
            " bootstrap and is not bounded",
            {},
            False,
            False,
        )
    try:
        probe_result = probe_fn()
    except Exception as exc:                              # noqa: BLE001
        probe_result = None
        probe_detail = "the readiness probe raised %s" % (
            exc.__class__.__name__,
        )
    else:
        probe_detail = None
    state, detail, pairs = probe_verdict(probe_result, prior_pairs)
    if probe_detail is not None:
        detail = "%s; %s" % (probe_detail, detail)
    if state == BOOTSTRAP_READY:
        return state, detail, pairs, True, False
    if not bootstrap_deadline_passed(entry, now, max_seconds):
        return state, detail, pairs, True, False
    bound = BOOTSTRAP_MAX_SECONDS if max_seconds is None else max_seconds
    if state == BOOTSTRAP_UNOBSERVABLE:
        # ABSENCE OF EVIDENCE DOES NOT STOP THE WORKFLOW. This layer
        # bounds only what it can POSITIVELY observe: a probe that
        # unable to see the target at all is a fact about the probe,
        # and blocking on it would make a mission that is running fine
        # but unreadable expire at the bootstrap bound — a deadline on
        # engineering wearing a bootstrap costume, which an existing
        # guarantee test (`test_no_mission_timer_behavioral`) caught
        # when an earlier draft of this function did stop here.
        #
        # The wait that remains is RECORDED, not silent: the
        # UNOBSERVABLE state is on the record durably from its first
        # occurrence. The residual, in the same breath: a target that
        # is genuinely stuck at a startup prompt AND unobservable is
        # not distinguished from a healthy unobservable one; within
        # this layer no readable signal separates them.
        return (
            BOOTSTRAP_UNOBSERVABLE,
            "the target has NOT been observable for the whole %ds"
            " bootstrap window (%s); this is recorded durably and does"
            " NOT stop the workflow, because bounding on absence of"
            " evidence would expire a mission that is merely"
            " unreadable" % (bound, detail),
            pairs,
            True,
            False,
        )
    return (
        BOOTSTRAP_FAILED,
        "the target did not reach readiness within the %ds bootstrap"
        " window (%s); an interactive startup prompt or an"
        " unregistered role is a bootstrap failure, not engineering"
        % (bound, detail),
        pairs,
        True,
        True,
    )


def problem_for(state):
    """The durable problem code for a stopping readiness state, or
    None for a state that does not stop the workflow. Only
    ``BOOTSTRAP_FAILED`` stops."""
    if state == BOOTSTRAP_FAILED:
        return PROBLEM_BOOTSTRAP_INCOMPLETE
    return None
