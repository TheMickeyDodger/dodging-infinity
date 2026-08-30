"""Durable TURN OUTCOME — what happened, recorded where it happened.

WHY `agent_status` IS INSUFFICIENT AS THE OBSERVABLE
----------------------------------------------------

R-55 AS-1. A turn can end five ways and `agent_status` distinguishes
three of them badly: a turn still RUNNING, a turn INTERRUPTED
mid-flight, and a turn whose transport DIED all present as an agent
that is not reporting done. Within this suite reading one of them as
another is how a control message accepted by the transport and left
unacted-upon looked healthy, and how a wait that returned success on
an already-done agent reported "finished this round" when it meant
"still finished from last time".

So the outcome is RECORDED, not inferred. Five values, disjoint:

    running           the turn is open; this record was written BEFORE
                      the work, so an interruption leaves evidence
    completed         the turn ended AND its expected artifact exists
    blocked           the turn ended deliberately without doing the work
    failed_transport  the turn failed to reach its agent, or the reply
                      failed to come back — a BOOTSTRAP/LIFECYCLE
                      failure
    interrupted       the turn was cut off; no decision was reached

ABSENCE OF EVIDENCE IS NOT HEALTH
---------------------------------

AS-2. A turn that terminates without its expected artifact leaves a
FAILURE RECORD naming the cause, AT THE POINT OF TERMINATION. Within
this module "no artifact" is barred from being the only evidence of
failure — that is the sentence `identity.classify` already carried and
did not honour until I2c, arriving here. `close` REFUSES to record
`completed` when the expected artifact is absent, so within it a
missing artifact is unwritable as a success.

NO ENGINEERING-MISSION TIMEOUT LIVES HERE
-----------------------------------------

AS-3. A transport-dead turn is a bootstrap/lifecycle failure, not
engineering duration, and this module holds NO deadline on a turn:
within it the record says what happened, and no line decides that a
turn has taken too long. `failed_transport` is raised by whatever owns
the transport, in the same vocabulary `target_runtime.readiness` uses
for a bootstrap that could not be observed. A duration bound
introduced here would be a mission deadline wearing a bootstrap
costume, which I3's pinned test exists to reject.

ROUTED IS NOT DELIVERED
-----------------------

AS-4. Two separate facts, and within this record they stay separate.
R-26 found that authority is not enacted state; R-49 found a queued ruling that arrived too late to protect what it
named; and this increment adds the third mode — RECEIVED, STARTED, THEN ANNIHILATED. A
turn that was routed and whose EFFECT went unobserved is not
delivered, and `delivered` says so from the two facts rather than from
the sending alone.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

TURN_RUNNING = "running"
TURN_COMPLETED = "completed"
TURN_BLOCKED = "blocked"
TURN_FAILED_TRANSPORT = "failed_transport"
TURN_INTERRUPTED = "interrupted"

TURN_OUTCOMES = (
    TURN_RUNNING,
    TURN_COMPLETED,
    TURN_BLOCKED,
    TURN_FAILED_TRANSPORT,
    TURN_INTERRUPTED,
)

#: Outcomes that mean the turn is over.
TERMINAL_OUTCOMES = (
    TURN_COMPLETED,
    TURN_BLOCKED,
    TURN_FAILED_TRANSPORT,
    TURN_INTERRUPTED,
)

#: Outcomes that are FAILURES and must name a cause.
FAILURE_OUTCOMES = (
    TURN_BLOCKED,
    TURN_FAILED_TRANSPORT,
    TURN_INTERRUPTED,
)

TURNS_FILE = "turns.json"
TURNS_VERSION = 1


class TurnRecordError(ValueError):
    """A turn outcome that must not be writable was attempted."""


#: The modules whose code decides a turn's outcome. A record written
#: by one build and read under another was made by DIFFERENT LOGIC,
#: and until this existed no instrument here could say so.
OBSERVER_SOURCES = ("turns.py", "heartbeat.py")


def observer_build():
    """A short fingerprint of the code that derives turn outcomes.

    VERSION SKEW IS INVISIBLE TO EVERY OTHER INSTRUMENT HERE, and this
    mission produced the specimen while I6 was being written: for
    eight minutes the code on disk said the observer was wired and the
    RUNNING controller was a process started before that edit, so the
    surface and the source disagreed and no check anywhere reported
    it. A durable claim that depends on a running process has to say
        which BUILD that process was running, or a reader comparing it
    against today's source compares two different programs.

        Returns None for a source it is unable to read; within this
    record an unknown build is stored as unknown rather than as a
    match.
    """
    import hashlib
    from pathlib import Path as _Path
    digest = hashlib.sha256()
    here = _Path(__file__).resolve().parent
    for name in OBSERVER_SOURCES:
        try:
            digest.update((here / name).read_bytes())
        except OSError:
            return None
    return digest.hexdigest()[:12]


def turns_path(herd_root):
    return Path(herd_root) / "state" / TURNS_FILE


def load_turns(herd_root):
    """Every recorded turn, or an empty document.

    Each failure decided separately, on I2c's discipline: ABSENT is a
    herd that has recorded no turn yet and returns empty; anything
    present that does not parse or is not the document shape RAISES,
    because rebuilding it destroys the only evidence that something
    wrote it wrong.
    """
    path = turns_path(herd_root)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {"version": TURNS_VERSION, "turns": []}
    except OSError as exc:
        raise TurnRecordError(
            "the turn record at %s could not be read (%s); an"
            " unreadable file is not an absent one" % (path, exc)
        )
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise TurnRecordError(
            "the turn record at %s does not parse (%s); it is REFUSED"
            " rather than rebuilt" % (path, exc)
        )
    if not isinstance(document, dict) or not isinstance(
        document.get("turns"), list
    ):
        raise TurnRecordError(
            "the turn record at %s is not a turn document" % (path,)
        )
    return {"version": TURNS_VERSION, "turns": document["turns"]}


def save_turns(herd_root, document):
    """Write the turn record ATOMICALLY.

    Same shape as `identity.save_bindings`, and for the same reason: a
    half-written outcome record reads as authoritative for the turns it
    names and silently omits the rest. Temp file in the same directory,
    fsync, `os.replace`, directory fsync.

    NOT a destructive site: within this function no existing record
    is removed.
    `os.replace` supersedes the previous document atomically and no
    `unlink` is performed, so W-4's enumeration is unchanged by this
    module.
    """
    path = turns_path(herd_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".%d.partial" % os.getpid())
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


def new_turn(turn_id, task_id, logical, expected_artifact=None,
             now=None):
    """A turn record in the `running` state.

    WRITTEN BEFORE THE WORK (K-1). A turn interrupted between
    starting and finishing leaves this record behind, which is what
    makes `interrupted` distinguishable from `running` at all — with
    no record, an interrupted turn and one that failed to start look
    identical.
    """
    for name, value in (("turn id", turn_id), ("task id", task_id),
                        ("logical role", logical)):
        if not isinstance(value, str) or not value:
            raise TurnRecordError(
                "a turn record requires a %s; a turn nothing can be"
                " attributed to is the state this record exists to"
                " end" % name
            )
    return {
        "turn_id": turn_id,
        "task_id": task_id,
        "logical": logical,
        "outcome": TURN_RUNNING,
        "cause": None,
        "expected_artifact": expected_artifact,
        "artifact_present": None,
        "opened_at": int(time.time() if now is None else now),
        "closed_at": None,
        # WHICH BUILD MADE THIS CLAIM. Not decoration: a record
        #     written by a controller running older logic is a claim from
        # a different program, and within this record a reader unable
        # to tell compares it against source it did not run.
        "observer_build": observer_build(),
        # AS-4: two facts, kept apart.
        "routed_at": None,
        "effect_observed_at": None,
    }


def close(record, outcome, cause=None, artifact_present=None,
          now=None):
    """Close a turn with a RECORDED outcome. Returns a new record.

    THE CONSTRUCTION HALF, and it is three refusals:

    * an unknown outcome is not writable;
    * a FAILURE outcome with no cause is not writable — "it failed"
      without a reason is the absence of evidence AS-2 forbids, and a
      record that says only `blocked` sends a reader back to the
      artifacts to guess;
    * `completed` with its expected artifact ABSENT is not writable.
      That is the whole of AS-2 in one guard: a turn that terminated without producing what it was for is not
      written down as a success, so "no artifact" is the only evidence
      of failure.
    """
    if outcome not in TURN_OUTCOMES:
        raise TurnRecordError(
            "%r is not a turn outcome; the five are %s"
            % (outcome, ", ".join(TURN_OUTCOMES))
        )
    if outcome in FAILURE_OUTCOMES and not (
        isinstance(cause, str) and cause
    ):
        raise TurnRecordError(
            "a %s turn must name its CAUSE at the point of"
            " termination (R-55 AS-2); a failure with no reason"
            " recorded sends the next reader back to the artifacts to"
            " guess" % outcome
        )
    expected = record.get("expected_artifact")
    if outcome == TURN_COMPLETED and expected and not artifact_present:
        raise TurnRecordError(
            "a turn expecting %r cannot be recorded COMPLETED while"
            " that artifact is absent: absence of evidence is not"
            " health, and the outcome here is a FAILURE RECORD naming"
            " the cause (R-55 AS-2)" % (expected,)
        )
    closed = dict(record)
    closed["outcome"] = outcome
    closed["cause"] = cause
    closed["artifact_present"] = artifact_present
    closed["closed_at"] = int(time.time() if now is None else now)
    return closed


#: Statuses a probe SYNTHESISES when it could not reach the agent.
#: They are evidence of a TRANSPORT problem, not of a busy agent —
#: `identity.PROBE_SENTINELS` already draws that line and this reads
#: the same one rather than a second copy of it.
def transport_reachable(status):
    from .identity import PROBE_SENTINELS
    return status not in PROBE_SENTINELS


#: Statuses that mean the agent is doing the turn right now.
WORKING_STATUSES = ("working",)

#: Statuses that mean the agent has stopped doing it.
SETTLED_STATUSES = ("idle", "done", "blocked")

#: Why an open turn was left open. Recorded on the record, so a turn
#: that stays RUNNING says WHY rather than looking like an oversight.
EVIDENCE_INSUFFICIENT = (
    "the observed status is not one this observer can read as working"
    " or settled, so the evidence does not distinguish a long"
    " legitimate turn from a dead one; the honest outcome is RUNNING"
)


def outcome_from(status, transport_ok, artifact_present,
                 interrupted=False):
    """Derive an outcome from EVERY input it depends on.

    AS-1's construction half. `agent_status` alone conflates RUNNING, INTERRUPTED and
    FAILED_TRANSPORT, so there is deliberately no function here taking
    only a status — a caller that has just the status has not observed
    enough to name an outcome, and the way this module says so is by
    having no such function to call.

    Order is load-bearing: transport is asked FIRST, because a turn that reached its agent
    has no agent status worth reading, and reading one anyway is how a
    transport failure became "the agent is busy".
    """
    if not transport_ok:
        return TURN_FAILED_TRANSPORT
    if interrupted:
        return TURN_INTERRUPTED
    if status in ("working", None):
        return TURN_RUNNING
    if artifact_present is None:
                # NO ARTIFACT WAS EXPECTED. A control-role turn often produces
        # no named file — a contract re-seed, a heartbeat prompt — and
        # within that case reading "no artifact" as BLOCKED would
        # manufacture a failure out of a turn with no output to
        # produce. Absent expectation and absent artifact are
        # different facts.
        return TURN_COMPLETED
    if artifact_present:
        return TURN_COMPLETED
    return TURN_BLOCKED


# --- THE OBSERVER (R-63 BA-5) ----------------------------------------
#
# Within this design a turn killed by transport is unable to write its
# own epitaph. The dying party does not run, so within any design
# recording termination from the terminating turn, exactly the case
# that motivated AS-1 fails. R-49's lesson generalized: a protective
# action unable to execute when needed offers no protection at all.
#
# So the outcome is DERIVED BY SOMETHING THAT SURVIVES THE DEATH. On
# this herd that is the heartbeat controller: a separate process that
# already probes every control role on every pass, and that keeps
# running when a role's turn dies.
#
# NO CLOCK PARTICIPATES. `derive` takes status, transport reachability
# and artifact presence — within its signature there is no elapsed
# time, so a long legitimate engineering turn and a dead one are
# separated by EVIDENCE alone. When the evidence does
# not separate them the turn stays RUNNING and says why. That is the
# honest answer, and it is the one R-63 requires instead of a guess.


def derive(record, status, artifact_present=None, interrupted=False):
    """``(outcome, cause)`` for an OPEN turn, from evidence only.

    ``outcome`` is None when the turn should stay open. Returns a
    CAUSE for every failure outcome, because AS-2 requires the cause
    at the point of termination and this observer IS that point — the
    turn itself is gone.
    """
    if not transport_reachable(status):
        return TURN_FAILED_TRANSPORT, (
            "the probe for `%s` returned %r: the agent could not be"
            " reached, so this turn ended in TRANSPORT FAILURE rather"
            " than in a decision. Derived by the observer, because a"
            " turn killed by transport cannot record its own end"
            % (record.get("logical"), status)
        )
    if interrupted:
        return TURN_INTERRUPTED, (
            "the turn for `%s` was cut off before any outcome was"
            " decided" % (record.get("logical"),)
        )
    if status in WORKING_STATUSES:
        return None, None
    if status not in SETTLED_STATUSES:
        return None, EVIDENCE_INSUFFICIENT
    outcome = outcome_from(status, True, artifact_present)
    if outcome == TURN_BLOCKED:
        return TURN_BLOCKED, (
            "`%s` settled at %r and its expected artifact %r is"
            " ABSENT; the turn ended without producing what it was"
            " for" % (record.get("logical"), status,
                      record.get("expected_artifact"))
        )
    return outcome, None


def open_turn_for(logical, task_id, turn_id, expected_artifact=None,
                  now=None):
    """The beginning of a turn, made durable BEFORE its outcome."""
    return new_turn(turn_id, task_id, logical,
                    expected_artifact=expected_artifact, now=now)


def open_turn_of(document, logical):
    """The one OPEN turn for this role, or None."""
    for entry in reversed(document.get("turns", [])):
        if (isinstance(entry, dict) and entry.get("logical") == logical
                and entry.get("outcome") == TURN_RUNNING):
            return entry
    return None


def observe_control_roles(herd_root, agents, task_id, prober,
                          expected=None, turn_id_factory=None,
                          artifact_probe=None, now=None):
    """ONE OBSERVER PASS over the control roles. Returns events.

    THE PRODUCTION PATH R-63 BA-4 requires: it opens a turn when a
    role starts working and closes it, from evidence, when the role
    stops — so a turn's BEGINNING and its OUTCOME both become durable
    without the turn itself having to survive to write either.

    Idempotent by construction: a role already working with an open
    turn produces no event, so a controller that runs every few
    seconds does not churn the record.
    """
    if not isinstance(task_id, str) or not task_id:
        from .vintage import TaskScopeRequired
        raise TaskScopeRequired(
            "a turn observation requires the task it belongs to; an"
            " unattributed turn record is task-mixed by construction"
            " (R-59 AW-2)"
        )
    expected = expected or {}
    make_id = turn_id_factory or _default_turn_id
    document = load_turns(herd_root)
    events = []
    changed = False
    for logical in sorted(agents):
        agent = agents[logical]
        probe = prober(agent)
        status = (probe or {}).get("status")
        record = open_turn_of(document, logical)
        if record is None:
            if status in WORKING_STATUSES:
                opened = open_turn_for(
                    logical, task_id, make_id(logical),
                    expected_artifact=expected.get(logical), now=now,
                )
                opened["agent"] = agent
                document["turns"].append(opened)
                events.append(("opened", logical, opened["turn_id"]))
                changed = True
            continue
        present = None
        if record.get("expected_artifact") and artifact_probe:
            present = bool(artifact_probe(record["expected_artifact"]))
        outcome, cause = derive(record, status,
                                artifact_present=present)
        if outcome is None:
            if cause and record.get("cause") != cause:
                record["cause"] = cause
                changed = True
            continue
        closed = close(record, outcome, cause=cause,
                       artifact_present=present, now=now)
        document["turns"] = [
            closed if entry is record else entry
            for entry in document["turns"]
        ]
        events.append((outcome, logical, closed["turn_id"]))
        changed = True
    if changed:
        save_turns(herd_root, document)
    return events, document


def _default_turn_id(logical):
    import secrets
    return "%s-%s" % (logical, secrets.token_hex(4))


def role_state(document, logical):
    """This role's CURRENT turn, its last TRANSITION and its RECOVERY
    state, from durable state alone (R-63 BA-4).

    `recovery` is what a restarting reader needs: a role whose last
    recorded turn ended in transport failure or was interrupted has
    something to recover, and one that completed does not.
    """
    entries = [
        entry for entry in document.get("turns", [])
        if isinstance(entry, dict) and entry.get("logical") == logical
    ]
    if not entries:
        return {"logical": logical, "current": None,
                "last_outcome": None, "recovery": "no_turn_recorded"}
    last = entries[-1]
    open_now = last.get("outcome") == TURN_RUNNING
    recovery = "none"
    if last.get("outcome") in (TURN_FAILED_TRANSPORT, TURN_INTERRUPTED):
        recovery = "needs_recovery"
    elif last.get("outcome") == TURN_BLOCKED:
        recovery = "blocked_needs_decision"
    elif open_now:
        recovery = "in_flight"
    return {
        "logical": logical,
        "current": last["turn_id"] if open_now else None,
        "last_outcome": last.get("outcome"),
        "last_cause": last.get("cause"),
        "recovery": recovery,
        "delivered": delivered(last),
    }


def mark_routed(record, now=None):
    """Record that the turn was ROUTED. Not that it was delivered."""
    routed = dict(record)
    routed["routed_at"] = int(time.time() if now is None else now)
    return routed


def mark_effect_observed(record, now=None):
    """Record that the turn's EFFECT was observed.

    Separate from `mark_routed` because they are separate facts and
    the gap between them is the failure mode: routed-then-unenacted is the third delivery mode this increment
    names, after R-26's authority-is-not-state and R-49's ruling that
    arrived too late to protect what it named.
    """
    if not record.get("routed_at"):
        raise TurnRecordError(
            "an effect cannot be observed for a turn that was never"
            " routed; recording one would collapse the two facts AS-4"
            " keeps apart"
        )
    seen = dict(record)
    seen["effect_observed_at"] = int(time.time() if now is None else now)
    return seen


# --- R-62 AZ-4: the THIRD AXIS — IS THIS IN FORCE? -------------------
#
# Liveness asks IS THIS TRUE. Vintage asks WHEN IS THIS TRUE OF. This
# asks IS THIS IN FORCE, and it is a different question from both.
#
# The specimen is the supervisor's own, recorded the day this was
# written: a message named the increment, gave the complete
# requirement set, told the lead to brief — AND attached a hold whose
# release depended on a further message, which went unsent. The
# instruction was DELIVERED and it was not IN EFFECT. AM-3 had
# already required that distinction after R-49, where a preservation
# ruling arrived too late to preserve anything; this is the second
# instance, and it adds the paired one: AUTHORIZED is not GATED.
#
# A hold and its own release are not writable in one instruction here,
# for the same reason: an instruction that authorizes AND withholds is
# two states at once, and a reader takes whichever half is louder.

FORCE_DELIVERED = "delivered"
FORCE_IN_EFFECT = "in_effect"
FORCE_AUTHORIZED = "authorized"
FORCE_GATED = "gated"

FORCE_STATES = (
    FORCE_DELIVERED, FORCE_IN_EFFECT, FORCE_AUTHORIZED, FORCE_GATED,
)


def instruction_force(delivered_at=None, effect_observed_at=None,
                      authorized=False, gated_on=None):
    """``(state, detail)`` for one instruction. both halves.

    Four states, and the pairs are deliberately not collapsible:

      GATED       — a release condition is named and unmet. It does
                    not matter that it was delivered; it is not in
                    force, and a surface saying `delivered` here is
                    the exact mislead the specimen produced.
      AUTHORIZED — permitted, and unacted-on so far.
                    R-26: authority is not enacted state.
      DELIVERED   — it arrived and its EFFECT has not been observed.
                    R-49's queued ruling, and the third mode this
                    mission named: received, started, then annihilated.
      IN_EFFECT   — the effect was observed. Only this one means the
                    instruction is doing anything.
    """
    if gated_on:
        return FORCE_GATED, (
            "GATED on %s — delivered is not in force, and a surface"
            " that says otherwise misleads exactly as the specimen"
            " did" % (gated_on,)
        )
    if effect_observed_at:
        return FORCE_IN_EFFECT, "effect observed"
    if delivered_at:
        return FORCE_DELIVERED, (
            "delivered and its effect has NOT been observed; routed is"
            " not enacted (R-55 AS-4)"
        )
    if authorized:
        return FORCE_AUTHORIZED, (
            "authorized and unacted-on; authority is not enacted state"
            " (R-26)"
        )
    return FORCE_GATED, "neither delivered nor authorized"


def hold_and_release_together(gated_on, carries_requirements):
    """Whether one instruction both WITHHOLDS and instructs.

    The specimen's shape, made checkable: an instruction that attaches
    a hold AND carries actionable requirements is two states at once.
    Either it authorizes, or it withholds and carries no actionable
    requirements — a reader given both takes whichever half is louder,
    and proceeding on the actionable half is the REASONABLE reading.
    """
    return bool(gated_on) and bool(carries_requirements)


def delivered(record):
    """Whether the turn was DELIVERED — routed AND effect observed."""
    return bool(record.get("routed_at")) and bool(
        record.get("effect_observed_at")
    )


def append_turn(herd_root, record):
    """Append one turn record durably and atomically."""
    document = load_turns(herd_root)
    document["turns"].append(record)
    save_turns(herd_root, document)
    return document


def replace_turn(herd_root, record):
    """Supersede the stored record for this turn id, atomically."""
    document = load_turns(herd_root)
    turns = [
        entry for entry in document["turns"]
        if not (isinstance(entry, dict)
                and entry.get("turn_id") == record.get("turn_id"))
    ]
    turns.append(record)
    document["turns"] = turns
    save_turns(herd_root, document)
    return document


def turns_for_task(document, task_id):
    """Every recorded turn belonging to THIS task. TASK-SCOPED.

    AW-2's rule applied to this record too: the document holds turns
    from every task that has run in this herd, and selecting without a
    scope returns a confident wrong answer.
    """
    if not isinstance(task_id, str) or not task_id:
        from .vintage import TaskScopeRequired
        raise TaskScopeRequired(
            "turn selection requires a task id; the turn record is"
            " task-mixed (R-59 AW-2)"
        )
    return [
        entry for entry in document.get("turns", [])
        if isinstance(entry, dict) and entry.get("task_id") == task_id
    ]


def outcome_counts(records):
    """``{outcome: count}`` over exactly the records given.

    The DOMAIN is the list passed in — this reads no directory of its own
    — so a caller that scoped its selection wrongly is unable to have
    that hidden by a helper that quietly re-read the whole document.
    """
    counts = {outcome: 0 for outcome in TURN_OUTCOMES}
    for entry in records:
        if isinstance(entry, dict) and entry.get("outcome") in counts:
            counts[entry["outcome"]] += 1
    return counts
