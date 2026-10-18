"""Read-only runtime snapshot service: the producer of the injected
``di.mission_sources.runtime_snapshot`` value (increment 2, part 1).

``mission_sources`` never imports the runtime engine; it validates an
INJECTED snapshot value. This module, inside ``target_runtime`` (the only
package permitted to import the engine, and unreachable from the control
chain), takes that snapshot. It writes NOTHING: no engine state and no file.
A long-lived in-process caller holds ONE ``SnapshotService`` and receives
the value from it; ``dirun snapshot`` makes one request on a fresh owner and
emits the value on stdout as exactly one JSON value (the snapshot, or
``null`` when none was taken), with every diagnostic on stderr.

What it reads, and nothing else
===============================

- For each EXPLICITLY REGISTERED herd root — an absolute, already-resolved
  directory the caller names; nothing is discovered, scanned or guessed — the
  three fixed engine state artifacts ``.herd/state/task.json``,
  ``runtime.json`` and ``role-bindings.json``, each through the one hardened
  reader ``state_artifact.read_state_artifact`` (no-follow non-blocking open,
  regular file, single link, directory chain re-checked after the read,
  64 KiB bound).
- ONE listing process, run with the argv the caller passes (a REQUIRED
  argument with no default; production passes ``LISTING_ARGV``, the engine's
  read-only ``herdr agent list``), through the engine's own owned runner.

What it never does
==================

No spawn, restore, restart, archive, edit, message, terminal-input or queue
path of the engine is entered; the only process started is that read-only
listing. The projection is an ALLOWLIST (the ``herdr.observe._probe_one``
discipline): per live agent exactly name, provider, status, session, pane and
workspace; nothing else from the payload (cwd, terminal ids, any future
field) reaches the snapshot.

The owner and its caller contract
=================================

``SnapshotService`` is THE supported owner: ONE long-lived instance, ONE
in-flight capture slot. Its caller contract (a contract about RETURN and
CAPACITY, not a claim that any OS operation finishes in bounded time):

- RESPONSIVE RETURN. ``request(wait_seconds)`` waits at most about
  ``wait_seconds`` (at most ``MAX_WAIT_SECONDS``) for its capture and then
  returns: a snapshot, or NO snapshot with a distinguishable ``problem``:
  ``capture_not_finished_within_wait`` (the capture is still running and
  keeps the slot), ``capture_slot_occupied`` (a previous capture's owned work
  is outstanding; nothing was started), ``capture_cleanup_unestablished``
  (an owned run's cleanup could not be shown), ``capture_input_exceeds_bound``
  or ``herd_roots_invalid`` (nothing was started), or the state and listing
  refusals below. The capture runs in the owner's worker thread; the caller
  only waits on it, so a stalled step strands the WORKER, never the caller.
- ONE SLOT, NO QUEUE. The slot is taken BEFORE anything is started (so a
  stalled ``Popen`` already holds it). A request while it is held starts
  nothing and spawns no replacement: it returns ``capture_slot_occupied``.
- RELEASE ONLY ON THE CLEANUP CONDITION (``_cleanup_condition``). Returning
  to the caller and releasing the slot are SEPARATE events: a caller that
  stopped waiting has not ended the owned work, and a lingering worker
  cannot release capacity. The slot is released only when, for every run the
  capture started, either the runner gave its EXACT native not-started
  report (nothing created), or the run's leader WAS REAPED and its process
  group was then observed ABSENT (``ESRCH``) by existence-only observations
  within ``ABSENCE_WINDOW_SECONDS`` (the first absence ends them). Anything
  else fails CLOSED and keeps the slot: a missing, malformed, contradictory
  or non-mapping runner result, an unreaped leader, a group id that exists
  or cannot be observed (after the reap the id may have been reused, so
  existence is never read as "ours"), an interrupted worker start, and a
  defect. When the ONLY open question is a reaped run's group that existed,
  ONE later existence-only observation of its ABSENCE, when the owner is
  next asked (``request`` or ``settle``), settles it; no other hold is ever
  released, and nothing is ever signalled after a reap.
- NO FRESH SUCCESS WHILE CLEANUP IS UNSATISFIED: every run of a capture
  passes the condition before its outcome is used and before the next run
  starts, and a held slot refuses every later request.
- NOTHING ACCUMULATES WITHIN ONE OWNER: at most one capture's owned work at a
  time, structurally (one slot), not by a timer.
- LIFETIMES DIFFER, HONESTLY. A long-lived owner's ``shutdown()`` refuses
  all new work (``capture_owner_shut_down``) and keeps responsibility for
  existing work: ``diagnostics()`` reports it ``pending`` or ``uncertain``,
  never done. The ONE-SHOT ``capture`` instead shuts its fresh owner down
  and WAITS for that owner's work to end before returning (it has no later
  caller to stay responsive for); no time bound is claimed for that wait.
  Workers are not daemon threads, so interpreter exit never silently kills
  one mid-run.
- NO WRITES: the slot's state (free or occupied, since when, in which phase,
  why held) lives in memory and in ``diagnostics()`` only.

The supported caller is a synchronous, non-overlapping poller of ONE owner;
overlap within that owner is impossible, not merely discouraged. The slot
constrains ONLY what that owner instance starts. A fresh owner, and so every
one-shot call of ``capture`` (``dirun snapshot``), gets a fresh slot:
repeated one-shot calls are NOT bounded by one slot between them, and nothing
here proves a process-wide or host-wide bound.

Ownership accounting. A capture starts at most THREE processes, as SIBLINGS,
in order, each through the engine's owned runner
``herdr.runtime._bounded_command``: (1) the FIRST PASS (root validation and
every root's three files) in a boundary child running ``boundary_main``;
(2) the LISTING; (3) the SECOND PASS, the coherence comparison and the
PROJECTION, in a second boundary child. Each starts a NEW SESSION AND GROUP
of its own (the runner's ``start_new_session``), whose id is its leader's pid,
reported by the runner as ``process_group``. Its stdin is closed, its output
is capped at read time (64 KiB), and the runner SIGKILLs the whole group at
EOF or at its deadline (``DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS``, 5 s) BEFORE
reaping the leader (its own ordering, unchanged); only then does this module
observe the group. NOTHING IS NESTED: a boundary child starts no process
(pinned), and the listing is started by the owner itself, never inside a
boundary, so no inner session exists for a kill to miss. A child runs with
``-I`` and ``-B`` and imports this repository from the absolute path given.

Stated residuals, not engineered around:

- STARTUP is an allowance, not a bound: the runner's ``Popen`` blocks until
  the child execs. A stalled exec strands the WORKER (still holding the
  slot), not the caller.
- An UNINTERRUPTIBLE KERNEL READ (for example on a hung network mount) can
  keep a SIGKILLed member alive; its group then exists and the slot stays
  held; and a leader so stuck that the runner's 1 s reap wait expires is
  never observed at all and holds the slot for this owner's lifetime.
- A descendant that DETACHES itself (``setsid``, or ``setpgid`` into another
  group) is unreachable through the group and is not covered by the
  condition.
- The group observation is an INSTANT, not a quiescence guarantee.
- A ONE-SHOT ``capture`` waits for its owned work to END before it returns,
  so on a stalled exec or a kernel stall ``dirun snapshot`` itself waits,
  without a time bound; it never exits early and calls the work accounted.

Inputs are bounded in LENGTH as well as count, before anything is serialized
or started: each herd root (``MAX_ROOT_PATH_CHARS``), each listing argv
element (``MAX_ARGV_ELEMENT_CHARS``), and each boundary payload
(``MAX_PAYLOAD_BYTES``, below the per-argument limit some kernels impose).

The in-process helpers (``_state_pass``, ``_project``) are internal and
tested directly; they are NOT the service.

Time
====

``observed_at`` is the injected clock read by ``request`` BEFORE the slot is
taken and before anything starts: the earliest instant the observation can
reflect, so a consumer's <= 60 s readiness bound is measured from the
observation, never from when a file happened to be written, and NEVER
EXTENDED. A request returns a snapshot only when its capture finished within
the wait, so a returned snapshot is about as old as that wait at most (at
most ``MAX_WAIT_SECONDS`` = 30 s, half the 60 s window); the maximum age is
the caller's chosen wait, not one asserted constant. A capture that did not
finish yields no snapshot, and its late result is never returned to anyone.
The second boundary child stamps the owner's value into the snapshot, and the
owner ACCEPTS the child's value only when it carries that exact
``observed_at`` and this producer and is ``fresh`` by the contract's own
validator.

Failure direction
=================

Fails toward UNAVAILABLE. A listing that could not be started, exited
non-zero, timed out, was truncated, or is not exactly ONE JSON document
(diagnostics mixed into stdout included — nothing is ever extracted from
noise), a row it cannot read, a registered root whose state is refused,
malformed, inconsistent or changed during the capture, and any bound
exceeded each yield NO snapshot (``snapshot`` None with a stable
``problem``), never a partial one. "Could not look" (no snapshot) stays
distinct from "looked and found nothing" (a valid snapshot with no task or
no agent), after ``target_runtime.readiness``.

Coherence (what the two passes do, and only that)
=================================================

Every root's three files are read in the first boundary, the listing is
taken, and every root's three files are read again in the second boundary; each file's (status, sha256) must be
equal across the passes, or the capture yields NO snapshot
(``herd_state_incoherent``). Equal passes show that NO INTERVENING VISIBLE
CHANGE to those bytes was observed. That is all they show: it is not an
atomic-generation guarantee, and a change reverted to byte-identical content
between the passes (ABA) is not detected. Coherence establishes NOTHING
about binding; binding comes only from the native records, as follows.

Binding (native definitions, consulted rather than reimplemented)
=================================================================

- CURRENT TASK: ``task.json`` is the engine's sole current-task authority
  (``herdr.vintage``, AJ-2): its ``id`` is the task running now. A task whose
  ``status`` is terminal by ``herdr.vintage.is_terminal_status`` is over, so
  the root contributes NO task; a root without ``task.json`` contributes none
  either. Both ``task.json`` and ``runtime.json`` must name this root as
  their repository.
- ROLE BINDING: a role is BOUND only when its ``role-bindings.json`` entry
  passes ``herdr.identity.binding_gap`` — the engine's SINGLE definition of
  bound (logical, agent and session present; a non-empty stable identity).
  An entry that does not — absent, an absent, empty or non-mapping stable
  identity included — is UNBOUND, exactly as native ``classify`` reads it,
  and is OMITTED: the snapshot never records a binding the engine does not
  hold. The roles considered are those ``runtime.json`` names; a bound entry
  for a different agent name than ``runtime.json`` names is likewise not a
  binding of that runtime role and is omitted.
- RECORDED IDENTITY, VERBATIM: the session and the stable pane and workspace
  come from the binding entry itself and are NEVER inherited from
  ``runtime.json``. ``runtime.json`` only cross-checks them: where it names
  a pane or workspace that differs from the recorded one, both are emitted
  as unknown, which ``mission_sources.roster`` never classifies as present.
  A stable identity that lacks pane or workspace (``binding_strength``
  ``partial``) is emitted with those unknown as well — stricter than native
  ``classify``, never looser.
- NATIVE VERDICT, NEVER LOOSER: the snapshot does not carry every stable
  field the engine compares (``cwd`` is not in the contract), so for each
  bound role whose agent name has exactly ONE live row the engine's own
  ``herdr.identity.classify`` is run against that raw row; a ``degraded``
  verdict (a recorded stable field disagrees, or the live record carries no
  session) is emitted with pane and workspace unknown, so the roster cannot
  call present what the engine calls degraded. The raw rows are used for
  that comparison only and never reach the snapshot.
- TASK ASSOCIATION IS DERIVED, NOT RECORDED (stated, not glossed): the
  engine records NO durable per-session task provenance — neither
  ``role-bindings.json`` nor ``runtime.json`` carries a task id or a
  generation linking it to a task. The association of a root's bound roles
  with its current task is DERIVED from task.json's authority over that same
  root. Consequences, explicitly: when the task is REPLACED, the new id is the
  task and the previous task id has no task in the snapshot (its Mission's
  members disappear rather than carry over); a RETAINED or stale
  ``runtime.json`` cannot make anyone present, because presence downstream
  requires a LIVE listed agent matching name, pane, workspace and session, so
  it yields ``disappeared`` or ``ambiguous``.
- ROLE NAME IS DERIVED: the engine records no role per agent; ``role`` is
  derived from the engine's own logical naming (``supervisor``, ``lead<n>``,
  ``executor<n>``, ``reviewer<n>`` — exactly what ``herdr.lifecycle``
  creates); any other logical name gets no role, which the roster classifies
  ``ambiguous``.

Self-check
==========

The produced value is validated by ``mission_sources``' own
``read_runtime_snapshot`` and must be ``fresh`` at its own ``observed_at``;
the shape is that contract's, never a looser one.
"""

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

from herdr import identity as engine_identity
from herdr import runtime as engine_runtime
from herdr import vintage as engine_vintage
from mission_sources import contract as snapshot_contract
from mission_sources import contract_v2 as chain_contract
from mission_sources import fileread
from target_runtime import prepare as prepare_module
from target_runtime import review_record
from target_runtime import state_artifact

PRODUCER = "target_runtime.runtime_snapshot"
STATE_SUBDIRS = (".herd", "state")
TASK_FILE = "task.json"
RUNTIME_FILE = "runtime.json"
BINDINGS_FILE = "role-bindings.json"
STATE_FILES = (TASK_FILE, RUNTIME_FILE, BINDINGS_FILE)
#: Query contract version 2 only (a CHAIN request): the fourth fixed state
#: file, read through the same hardened reader, never raised on.
CHILDREN_FILE = chain_contract.CHILDREN_FILE
STATE_FILES_V2 = STATE_FILES + (CHILDREN_FILE,)
RUNTIME_STATE_VERSION = 2
#: The production listing: the engine's read-only agent listing.
LISTING_ARGV = ("herdr", "agent", "list")

#: Independent bounds, never derived from input.
MAX_HERD_ROOTS = 16
MAX_LISTING_ARGV = 8
#: Input LENGTH bounds (count bounds above), enforced before anything is
#: serialized or started.
MAX_ARGV_ELEMENT_CHARS = 4096
MAX_ROOT_PATH_CHARS = 4096
#: The serialized payload handed to one boundary child (as one argv
#: element): roots plus, for the projection, digests and the listing (which
#: the runner already caps at 64 KiB).
MAX_PAYLOAD_BYTES = 126976
#: ``request(stable_names=...)``: the agent names whose listing rows' ONE
#: stable field the snapshot does not carry (``cwd``, part of the engine's
#: stable identity, ``herdr.identity.STABLE_FIELDS``) the projection also
#: returns, in memory only (``Capture.native_cwd``; never in a snapshot or
#: an answer). Bounded in count and in serialized bytes, so the payload
#: stays inside ``MAX_PAYLOAD_BYTES`` beside a full listing; the caller
#: chooses within the bound and discloses what it did not request.
MAX_STABLE_NAMES = 512
MAX_STABLE_NAMES_BYTES = 16384
#: The longest a caller may ask ``request`` to wait (half the consumer's
#: 60 s readiness window).
MAX_WAIT_SECONDS = 30
#: The one-shot ``capture`` wait (``dirun snapshot``): longer than the
#: supported path needs (three runs, each at most the runner's 5 s deadline
#: plus its 1 s reap wait), so only an unsupported stall outlasts it.
ONE_SHOT_WAIT_SECONDS = 30
#: After a reap, how long the existence-only observations may continue
#: before the condition is UNKNOWN: a member SIGKILLed with the group can
#: stay a zombie briefly until its new parent reaps it. The first ABSENCE
#: ends the window.
ABSENCE_WINDOW_SECONDS = 1.0
ABSENCE_POLL_SECONDS = 0.02

#: The boundary children: this repository, imported by absolute path into an
#: isolated (``-I``) interpreter, entering ``boundary_main`` and nothing else.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_BOUNDARY_CODE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from target_runtime.runtime_snapshot import boundary_main\n"
    "sys.exit(boundary_main(sys.argv[2:]))\n"
)
MODE_PASS = "pass"
MODE_PROJECT = "project"

#: The logical names ``herdr.lifecycle`` creates, and the role each names.
_LOGICAL_ROLE = re.compile(
    r"\A(?:(supervisor)|(lead|executor|reviewer)[1-9][0-9]{0,2})\Z")

PROBLEM_ROOTS_INVALID = "herd_roots_invalid"
PROBLEM_STATE_REFUSED = "herd_state_refused"
PROBLEM_STATE_MALFORMED = "herd_state_malformed"
PROBLEM_STATE_INCONSISTENT = "herd_state_inconsistent"
PROBLEM_STATE_INCOHERENT = "herd_state_incoherent"
PROBLEM_LISTING_UNAVAILABLE = "agent_listing_unavailable"
PROBLEM_LISTING_TIMED_OUT = "agent_listing_timed_out"
PROBLEM_LISTING_TRUNCATED = "agent_listing_truncated"
PROBLEM_LISTING_MALFORMED = "agent_listing_malformed"
PROBLEM_EXCEEDS_BOUND = "snapshot_exceeds_bound"
PROBLEM_SELF_CHECK = "snapshot_self_check_failed"
PROBLEM_BOUNDARY_TIMED_OUT = "capture_boundary_timed_out"
PROBLEM_INPUT_TOO_LARGE = "capture_input_exceeds_bound"
PROBLEM_SLOT_OCCUPIED = "capture_slot_occupied"
PROBLEM_CAPTURE_PENDING = "capture_not_finished_within_wait"
PROBLEM_CLEANUP_UNESTABLISHED = "capture_cleanup_unestablished"
PROBLEM_OWNER_SHUT_DOWN = "capture_owner_shut_down"
PROBLEM_NO_ROOT_OBSERVABLE = "capture_no_root_observable"
PROBLEM_BOUNDARY_FAILED = "capture_boundary_failed"

#: The refusals a boundary child may report; anything else from a child is
#: itself a boundary failure.
_CHILD_PROBLEMS = frozenset({
    PROBLEM_ROOTS_INVALID, PROBLEM_STATE_REFUSED, PROBLEM_STATE_MALFORMED,
    PROBLEM_STATE_INCONSISTENT, PROBLEM_STATE_INCOHERENT,
    PROBLEM_LISTING_MALFORMED, PROBLEM_EXCEEDS_BOUND, PROBLEM_SELF_CHECK,
})


class _Refused(Exception):
    def __init__(self, problem, detail):
        super(_Refused, self).__init__(problem)
        self.problem = problem
        self.detail = detail


@dataclass(frozen=True)
class Capture(object):
    """One capture: a validated ``snapshot`` dict, or None with a stable
    ``problem`` and a short ``detail`` naming the root index and file (never
    file or listing content)."""
    snapshot: Optional[dict]
    problem: Optional[str]
    detail: Optional[str]
    observed_at: int
    #: The ROOT ACCOUNT when roots were discovered for this request:
    #: ``registered`` (strict), ``discovered`` (requested), ``captured``
    #: (observed), ``unavailable`` ([{root, problem, detail}]) and
    #: ``over_capacity`` (requested, never captured); None otherwise.
    roots: Optional[dict] = None
    #: A CHAIN request only (query contract version 2): the validated chain
    #: observation taken by the same capture as ``snapshot``; None otherwise.
    chain_observation: Optional[dict] = None
    #: With ``request(stable_names=...)``: ``{name: [cwd or None, one per
    #: listing row of that name, in listing order]}`` for every requested
    #: name the SAME listing carried (the snapshot's own rows for it); None
    #: otherwise. In memory only: the snapshot contract does not carry it.
    native_cwd: Optional[dict] = None

    def is_available(self):
        return self.snapshot is not None


def production_clock():
    """Whole seconds of wall-clock time, the unit ``observed_at`` carries."""
    return int(time.time())


def _require_roots(herd_roots):
    if type(herd_roots) not in (list, tuple) or not (
            1 <= len(herd_roots) <= MAX_HERD_ROOTS):
        raise _Refused(PROBLEM_ROOTS_INVALID,
                       "1..%d registered herd roots are required"
                       % MAX_HERD_ROOTS)
    seen = set()
    for index, root in enumerate(herd_roots):
        if (type(root) is not str or not root or "\x00" in root
                or not os.path.isabs(root)
                or os.path.realpath(root) != root
                or not os.path.isdir(root)):
            raise _Refused(PROBLEM_ROOTS_INVALID,
                           "root %d is not an absolute, resolved directory"
                           % index)
        if root in seen:
            raise _Refused(PROBLEM_ROOTS_INVALID,
                           "root %d is registered twice" % index)
        seen.add(root)
    return tuple(herd_roots)


def _require_argv(listing_argv):
    if type(listing_argv) not in (list, tuple) or not (
            1 <= len(listing_argv) <= MAX_LISTING_ARGV) or any(
                type(item) is not str or not item or "\x00" in item
                for item in listing_argv):
        raise TypeError("listing_argv must be 1..%d non-empty strings"
                        % MAX_LISTING_ARGV)
    return list(listing_argv)


# -- the listing -------------------------------------------------------------

def _text(value, location, optional=False):
    try:
        return snapshot_contract.require_text(value, location, optional)
    except snapshot_contract.ContractError as exc:
        raise _Refused(PROBLEM_LISTING_MALFORMED, str(exc))


def _listing_stdout(result):
    """The stdout of one FINISHED listing (service side), or a refusal."""
    if type(result) is not dict or result.get("started") is not True or (
            result.get("error") is not None):
        raise _Refused(PROBLEM_LISTING_UNAVAILABLE, "listing was not taken")
    if result.get("timed_out") is not False:
        raise _Refused(PROBLEM_LISTING_TIMED_OUT,
                       "listing did not finish within its deadline; its"
                       " process group was killed")
    if result.get("stdout_truncated_at_read") is not False:
        raise _Refused(PROBLEM_LISTING_TRUNCATED,
                       "listing output exceeded its read cap")
    code = result.get("returncode")
    if type(code) is not int or code != 0:
        raise _Refused(PROBLEM_LISTING_UNAVAILABLE,
                       "listing exited %r" % (code,))
    stdout = result.get("stdout")
    if type(stdout) is not str:
        raise _Refused(PROBLEM_LISTING_MALFORMED, "listing output is not text")
    return stdout


def _listing_rows(stdout):
    """``(agents, live)`` of one listing's stdout (inside the boundary): the
    allowlisted rows, and the raw rows by agent name for the native
    comparison only."""
    if type(stdout) is not str:
        raise _Refused(PROBLEM_LISTING_MALFORMED, "listing output is not text")
    try:
        # Exactly one JSON document; json rejects anything else around it.
        document = fileread.load_bounded_json(stdout.encode("utf-8"))
    except (fileread.JsonRefused, UnicodeError) as exc:
        raise _Refused(PROBLEM_LISTING_MALFORMED,
                       "listing output is not one JSON document (%s)"
                       % getattr(exc, "problem", type(exc).__name__))
    rows = document.get("result") if type(document) is dict else None
    rows = rows.get("agents") if type(rows) is dict else None
    if type(rows) is not list:
        raise _Refused(PROBLEM_LISTING_MALFORMED,
                       "listing carries no agents list")
    if len(rows) > snapshot_contract.MAX_SNAPSHOT_AGENTS:
        raise _Refused(PROBLEM_EXCEEDS_BOUND,
                       "listing has %d agents; the bound is %d"
                       % (len(rows), snapshot_contract.MAX_SNAPSHOT_AGENTS))
    # A row this cannot read degrades the WHOLE listing (R-32 X-1): a
    # silently skipped row would make a partial set read as complete.
    agents = [_agent(row, index) for index, row in enumerate(rows)]
    live = {}
    for row in rows:
        live.setdefault(row["name"], []).append(row)
    return agents, live


def _agent(row, index):
    location = "listing.agents[%d]" % index
    if type(row) is not dict:
        raise _Refused(PROBLEM_LISTING_MALFORMED, location + " is not an object")
    session = row.get("agent_session")
    if session is not None and type(session) is not dict:
        raise _Refused(PROBLEM_LISTING_MALFORMED,
                       location + ".agent_session is not an object")
    return {
        "agent": _text(row.get("name"), location + ".name"),
        "provider": _text(row.get("agent"), location + ".agent", True),
        "status": _text(row.get("agent_status"), location + ".agent_status"),
        "session": _text((session or {}).get("value"),
                         location + ".agent_session.value", True),
        "pane_id": _text(row.get("pane_id"), location + ".pane_id", True),
        "workspace_id": _text(row.get("workspace_id"),
                              location + ".workspace_id", True),
    }


# -- the registered herd roots ----------------------------------------------

def _state(root, index, name):
    """``(status, digest, value)`` of one fixed state artifact: the parsed
    JSON object, or None when the file is absent."""
    status, _count, digest, text = state_artifact.read_state_artifact(
        root, STATE_SUBDIRS, name)
    if status == prepare_module.INSTRUCTION_ABSENT:
        return status, None, None
    if status != prepare_module.INSTRUCTION_READ:
        raise _Refused(PROBLEM_STATE_REFUSED,
                       "root %d %s: %s" % (index, name, status))
    try:
        value = fileread.load_bounded_json(text.encode("utf-8"))
    except fileread.JsonRefused as exc:
        raise _Refused(PROBLEM_STATE_MALFORMED,
                       "root %d %s: %s" % (index, name, exc.problem))
    if type(value) is not dict:
        raise _Refused(PROBLEM_STATE_MALFORMED,
                       "root %d %s is not an object" % (index, name))
    return status, digest, value


def _state_pass(roots):
    """One complete pass over every root's three files."""
    return [{name: _state(root, index, name) for name in STATE_FILES}
            for index, root in enumerate(roots)]


def _state_text(value, index, name, field, optional=False):
    try:
        return snapshot_contract.require_text(
            value, "root %d %s %s" % (index, name, field), optional)
    except snapshot_contract.ContractError as exc:
        raise _Refused(PROBLEM_STATE_MALFORMED, str(exc))


def _same_root(value, root, index, name):
    path = _state_text(value, index, name, "repo")
    if not os.path.isabs(path) or os.path.realpath(path) != root:
        raise _Refused(PROBLEM_STATE_INCONSISTENT,
                       "root %d %s names another repository" % (index, name))


def _task(root, index, files, live):
    task = files[TASK_FILE][2]
    if task is None:
        return None
    task_id = _state_text(task.get("id"), index, TASK_FILE, "id")
    status = _state_text(task.get("status"), index, TASK_FILE, "status",
                         optional=True)
    _same_root(task.get("repo"), root, index, TASK_FILE)
    if engine_vintage.is_terminal_status(status):
        return None
    runtime = files[RUNTIME_FILE][2]
    bindings = ([] if runtime is None
                else _bindings(root, index, runtime, files[BINDINGS_FILE][2],
                               live))
    if len(bindings) > snapshot_contract.MAX_TASK_BINDINGS:
        raise _Refused(PROBLEM_EXCEEDS_BOUND,
                       "root %d has %d bindings; the bound is %d"
                       % (index, len(bindings),
                          snapshot_contract.MAX_TASK_BINDINGS))
    return {"task_id": task_id, "workspace_realpath": root,
            "bindings": bindings}


def _recorded(stable, field):
    value = stable.get(field)
    return value if type(value) is str and value else None


def _bindings(root, index, runtime, recorded, live):
    version = runtime.get("version")
    if type(version) is not int or version != RUNTIME_STATE_VERSION:
        raise _Refused(PROBLEM_STATE_MALFORMED,
                       "root %d %s version is not %d"
                       % (index, RUNTIME_FILE, RUNTIME_STATE_VERSION))
    _same_root(runtime.get("repo"), root, index, RUNTIME_FILE)
    agents = runtime.get("agents")
    panes = runtime.get("panes", {})
    if type(agents) is not dict or type(panes) is not dict:
        raise _Refused(PROBLEM_STATE_MALFORMED,
                       "root %d %s agents/panes are not objects"
                       % (index, RUNTIME_FILE))
    runtime_workspace = _state_text(runtime.get("workspace_id"), index,
                                    RUNTIME_FILE, "workspace_id", True)
    roles = {} if recorded is None else recorded.get("roles")
    if type(roles) is not dict:
        raise _Refused(PROBLEM_STATE_MALFORMED,
                       "root %d %s carries no roles object"
                       % (index, BINDINGS_FILE))
    result = []
    for logical in sorted(agents, key=repr):
        _state_text(logical, index, RUNTIME_FILE, "agents key")
        name = _state_text(agents[logical], index, RUNTIME_FILE,
                           "agents[%s]" % logical)
        runtime_pane = _state_text(panes.get(logical), index, RUNTIME_FILE,
                                   "panes[%s]" % logical, True)
        entry = roles.get(logical)
        if engine_identity.binding_gap(entry) is not None:
            continue                      # UNBOUND by the native definition
        if entry["logical"] != logical or entry["agent"] != name:
            continue                      # not a binding of THIS runtime role
        session = _state_text(entry["session"], index, BINDINGS_FILE,
                              "roles[%s].session" % logical)
        stable = entry["stable"]
        pane_id = _recorded(stable, "pane_id")
        workspace_id = _recorded(stable, "workspace_id")
        if (_recorded(stable, "name") not in (None, name)
                or runtime_pane not in (None, pane_id)
                or runtime_workspace not in (None, workspace_id)):
            # The records disagree on the stable identity: unknown, which
            # the roster never classifies as present.
            pane_id = workspace_id = None
        rows = live.get(name, ())
        if len(rows) == 1 and engine_identity.classify(
                logical, entry, {"status": rows[0].get("agent_status"),
                                 "raw": {"result": {"agent": rows[0]}}}
        ).verdict == engine_identity.VERDICT_DEGRADED:
            pane_id = workspace_id = None
        match = _LOGICAL_ROLE.match(logical)
        result.append({
            "logical": logical,
            "role": (match.group(1) or match.group(2)) if match else None,
            "agent": name,
            "session": session,
            "pane_id": pane_id,
            "workspace_id": workspace_id,
        })
    return result


# -- inside the boundary ----------------------------------------------------

def _digests(passes):
    return [[list(files[name][:2]) for name in STATE_FILES]
            for files in passes]


def _first_pass(payload):
    """The first pass, in a boundary child. Registered roots are STRICT
    (any refusal refuses the capture, as before). DISCOVERED roots, when the
    payload names any, are TOLERANT: each is validated and read on its own,
    and a refusal makes THAT root unavailable, with its problem, never the
    capture. Without discovered roots the value is exactly the strict
    digests, as before."""
    if payload.get("chains") is True:
        return _first_pass_chains(payload)
    roots = payload["roots"]
    strict = _digests(_state_pass(_require_roots(roots))) if roots else []
    if "discovered" not in payload:
        return strict
    accounts = []
    for root in payload["discovered"]:
        try:
            checked = _require_roots([root])
            accounts.append({"root": root,
                             "digests": _digests(_state_pass(checked))[0]})
        except _Refused as exc:
            accounts.append({"root": root, "problem": exc.problem,
                             "detail": exc.detail})
    return {"digests": strict, "discovered": accounts}


def _native_cwd(payload, live):
    """``{name: [cwd or None per row]}`` for the requested names the listing
    carries (``live``: the raw rows by name, inside the boundary only)."""
    out = {}
    for name in payload["stable_names"]:
        if name in live:
            out[name] = [_text(row.get("cwd"), "listing %s.cwd" % name, True)
                         for row in live[name]]
    return out


def _project(payload):
    """The second pass, the coherence comparison and the projection. Runs in
    the second boundary child; internal and directly testable, NOT the
    bounded service. With ``stable_names`` in the payload the value is
    ``{"snapshot", "native_cwd"}`` (``_native_cwd``); else the snapshot."""
    if payload.get("chains") is not None:
        return _project_chains(payload)
    roots = _require_roots(payload["roots"])
    second = _state_pass(roots)
    for index, (before, after) in enumerate(zip(payload["digests"],
                                                _digests(second))):
        for name, old, new in zip(STATE_FILES, before, after):
            if list(old) != new:
                raise _Refused(PROBLEM_STATE_INCOHERENT,
                               "root %d %s changed during the capture"
                               % (index, name))
    agents, live = _listing_rows(payload["listing"])
    observed_at = payload["observed_at"]
    tasks = [task for task in (
        _task(root, index, files, live)
        for index, (root, files) in enumerate(zip(roots, second)))
        if task is not None]
    snapshot = {
        "schema": snapshot_contract.SNAPSHOT_SCHEMA,
        "version": snapshot_contract.SNAPSHOT_VERSION,
        "observed_at": observed_at,
        "producer": PRODUCER,
        "tasks": sorted(tasks, key=lambda t: (t["task_id"],
                                              t["workspace_realpath"])),
        "agents": agents,
    }
    reading = snapshot_contract.read_runtime_snapshot(snapshot, observed_at)
    if reading.health != snapshot_contract.HEALTH_FRESH:
        raise _Refused(PROBLEM_SELF_CHECK, reading.problem)
    if "stable_names" in payload:
        return {"snapshot": snapshot,
                "native_cwd": _native_cwd(payload, live)}
    return snapshot


# -- query contract version 2: the CHAIN request, inside the boundary --------
#
# A chain request (``request(..., chains=...)``) reads the FOURTH fixed state
# file, the children record, at every captured root through the same
# hardened reader, never raising on it: its four outcomes are kept apart
# (``contract_v2``). It follows each captured root's recorded children for
# that root's CURRENT task ONE level down (depth 1): a child root is only
# a path a validated record names, validated exactly as a discovered root
# is, read TOLERANTLY, inside the same ``MAX_HERD_ROOTS`` capacity. The
# projection adds the chain observation: per root its current task (the
# herdr membership rule and the herdr dependency rule, separately), its
# children record and its canonical review record (``review_record``).

def _children_state(root, index):
    """``(status, digest, outcome)`` of one root's children record, where
    ``outcome`` is ``("record", object)`` or ``("problem", code)``."""
    status, count, digest, text = state_artifact.read_state_artifact(
        root, STATE_SUBDIRS, CHILDREN_FILE)
    if status == prepare_module.INSTRUCTION_ABSENT:
        return status, None, ("problem",
                              chain_contract.PROBLEM_CHILDREN_ABSENT)
    if status == prepare_module.INSTRUCTION_REFUSED_NON_UTF8:
        return status, digest, ("problem",
                                chain_contract.PROBLEM_CHILDREN_NOT_JSON)
    if status != prepare_module.INSTRUCTION_READ:
        return status, digest, ("problem",
                                chain_contract.PROBLEM_CHILDREN_REFUSED)
    if count == 0:
        return status, digest, ("problem",
                                chain_contract.PROBLEM_CHILDREN_ZERO_BYTES)
    try:
        value = fileread.load_bounded_json(text.encode("utf-8"))
    except fileread.JsonRefused:
        return status, digest, ("problem",
                                chain_contract.PROBLEM_CHILDREN_NOT_JSON)
    if type(value) is not dict:
        return status, digest, ("problem",
                                chain_contract.PROBLEM_CHILDREN_SHAPE)
    return status, digest, ("record", value)


def _state_pass_v2(roots):
    """One complete pass over every root's FOUR files."""
    passes = []
    for index, root in enumerate(roots):
        files = {name: _state(root, index, name) for name in STATE_FILES}
        files[CHILDREN_FILE] = _children_state(root, index)
        passes.append(files)
    return passes


def _digests_v2(passes):
    return [[list(files[name][:2]) for name in STATE_FILES_V2]
            for files in passes]


def _child_text(value):
    if value is None:
        return None
    return snapshot_contract.require_text(value, "children record field")


def _child_record(record):
    """One recorded child: its identity and declared agents, NEVER its
    recorded status (a spawn-time value, not lifecycle truth)."""
    dependency = record.get("dependency")
    if dependency is not None and type(dependency) is not bool:
        raise snapshot_contract.ContractError(
            snapshot_contract.PROBLEM_SNAPSHOT_SHAPE, "dependency")
    agents = record.get("agents")
    declared = None
    if type(agents) is dict and len(agents) <= (
            snapshot_contract.MAX_TASK_BINDINGS) and all(
                type(k) is str and type(v) is str for k, v in agents.items()):
        declared = [{"logical": _child_text(logical),
                     "agent": _child_text(agents[logical])}
                    for logical in sorted(agents)]
    return {"parent_task_id": _child_text(record.get("parent_task_id")),
            "child_task_id": _child_text(record.get("task_id")),
            "repo": _child_text(record.get("repo")),
            "workspace_id": _child_text(record.get("workspace_id")),
            "dependency": dependency,
            "declared_agents": declared}


def _children_projection(files):
    """The children record of one root, for ITS current task only."""
    def unavailable(problem, count=None, other=None):
        return {"state": chain_contract.CHILDREN_UNAVAILABLE,
                "problem": problem, "caveat": None, "record_count": count,
                "other_parent_count": other, "records": []}

    kind, value = files[CHILDREN_FILE][2]
    if kind == "problem":
        return unavailable(value)
    task = files[TASK_FILE][2]
    task_id = task.get("id") if task is not None else None
    if type(task_id) is not str or not task_id:
        return unavailable(chain_contract.PROBLEM_CHILDREN_NO_TASK)
    records = value.get("children")
    if type(records) is not list:
        return unavailable(chain_contract.PROBLEM_CHILDREN_SHAPE)
    matched, other = [], 0
    for record in records:
        if type(record) is not dict:
            return unavailable(chain_contract.PROBLEM_CHILDREN_SHAPE)
        parent = record.get("parent_task_id")
        if parent is not None and type(parent) is not str:
            return unavailable(chain_contract.PROBLEM_CHILDREN_SHAPE)
        if parent == task_id:
            matched.append(record)
        else:
            other += 1
    if len(matched) > chain_contract.MAX_CHILD_RECORDS:
        return unavailable(chain_contract.PROBLEM_CHILDREN_OVER_LIMIT,
                           len(matched), other)
    try:
        projected = [_child_record(record) for record in matched]
    except snapshot_contract.ContractError:
        return unavailable(chain_contract.PROBLEM_CHILDREN_SHAPE)
    if not projected:
        return {"state": chain_contract.CHILDREN_OBSERVED_EMPTY,
                "problem": None, "caveat": chain_contract.CAVEAT_WRITER_RESET,
                "record_count": 0, "other_parent_count": other,
                "records": []}
    return {"state": chain_contract.CHILDREN_OBSERVED, "problem": None,
            "caveat": None, "record_count": len(projected),
            "other_parent_count": other, "records": projected}


def _dependency_complete(status):
    """The herdr DEPENDENCY rule (``herdr.dependencies``: the child's status,
    upper-cased, must be exactly COMPLETE), which is NOT the membership
    rule: FAILED and ABANDONED end membership and satisfy nothing."""
    return type(status) is str and status.upper() == "COMPLETE"


def _root_task(index, task):
    if task is None:
        return None
    status = _state_text(task.get("status"), index, TASK_FILE, "status",
                         optional=True)
    return {"task_id": _state_text(task.get("id"), index, TASK_FILE, "id"),
            "status": status,
            "membership_ended": engine_vintage.is_terminal_status(status),
            "dependency_complete": _dependency_complete(status)}


def _tolerant_pass(root, index):
    """A TOLERANT root (discovered or child): validated and read on its own,
    with the projection's own task checks applied now, so a refusal makes
    THIS root unavailable and never refuses the capture later."""
    if len(root) > MAX_ROOT_PATH_CHARS:
        raise _Refused(PROBLEM_INPUT_TOO_LARGE,
                       "root %d exceeds its length bound" % index)
    checked = _require_roots([root])
    files = _state_pass_v2(checked)[0]
    _task(root, index, files, {})
    return files


def _first_pass_chains(payload):
    """The first pass of a chain request: registered roots strict,
    discovered roots tolerant (as without chains), then the children each
    captured root records for its current task, one level down, tolerant,
    within the ``capacity`` the owner states."""
    roots = payload["roots"]
    strict = _state_pass_v2(_require_roots(roots)) if roots else []
    captured = list(zip(roots, strict))
    accounts = []
    for root in payload["discovered"]:
        try:
            files = _tolerant_pass(root, len(captured))
            accounts.append({"root": root,
                             "digests": _digests_v2([files])[0]})
            captured.append((root, files))
        except _Refused as exc:
            accounts.append({"root": root, "problem": exc.problem,
                             "detail": exc.detail})
    seen = set(root for root, _ in captured)
    candidates = set()
    for _root, files in captured:
        for record in _children_projection(files)["records"]:
            if record["repo"] is not None and record["repo"] not in seen:
                candidates.add(record["repo"])
    candidates = sorted(candidates)
    capacity = payload["capacity"]
    children = []
    for root in candidates[:capacity]:
        try:
            files = _tolerant_pass(root, len(captured) + len(children))
            children.append({"root": root,
                             "digests": _digests_v2([files])[0]})
        except _Refused as exc:
            children.append({"root": root, "problem": exc.problem,
                             "detail": exc.detail})
    return {"digests": _digests_v2(strict), "discovered": accounts,
            "children": children, "child_over_capacity": candidates[capacity:]}


def _project_chains(payload):
    """The second pass of a chain request: the four-file coherence check,
    the version 1 snapshot over every captured root, and the chain
    observation, each self-checked by its own contract."""
    chains = payload["chains"]
    roots = _require_roots(payload["roots"])
    second = _state_pass_v2(roots)
    for index, (before, after) in enumerate(zip(payload["digests"],
                                                _digests_v2(second))):
        for name, old, new in zip(STATE_FILES_V2, before, after):
            if list(old) != new:
                raise _Refused(PROBLEM_STATE_INCOHERENT,
                               "root %d %s changed during the capture"
                               % (index, name))
    agents, live = _listing_rows(payload["listing"])
    observed_at = payload["observed_at"]
    tasks = [task for task in (
        _task(root, index, files, live)
        for index, (root, files) in enumerate(zip(roots, second)))
        if task is not None]
    snapshot = {
        "schema": snapshot_contract.SNAPSHOT_SCHEMA,
        "version": snapshot_contract.SNAPSHOT_VERSION,
        "observed_at": observed_at,
        "producer": PRODUCER,
        "tasks": sorted(tasks, key=lambda t: (t["task_id"],
                                              t["workspace_realpath"])),
        "agents": agents,
    }
    reading = snapshot_contract.read_runtime_snapshot(snapshot, observed_at)
    if reading.health != snapshot_contract.HEALTH_FRESH:
        raise _Refused(PROBLEM_SELF_CHECK, reading.problem)
    expected = chains["expected_reviews"]
    entries = []
    for index, (root, files) in enumerate(zip(roots, second)):
        task = files[TASK_FILE][2]
        entries.append({
            "root": root, "tier": chains["tiers"][index],
            "task": _root_task(index, task),
            "children": _children_projection(files),
            "review": review_record.canonical_review(root, task,
                                                     expected.get(root)),
        })
    value = {"schema": chain_contract.CHAIN_OBSERVATION_SCHEMA,
             "version": chain_contract.CHAIN_OBSERVATION_VERSION,
             "observed_at": observed_at, "producer": PRODUCER,
             "roots": entries, "unavailable": chains["unavailable"],
             "over_capacity": chains["over_capacity"]}
    chain_reading = chain_contract.read_chain_observation(value, observed_at)
    if chain_reading.health != snapshot_contract.HEALTH_FRESH:
        raise _Refused(PROBLEM_SELF_CHECK, chain_reading.problem)
    out = {"snapshot": snapshot, "chain_observation": value}
    if "stable_names" in payload:
        out["native_cwd"] = _native_cwd(payload, live)
    return out


def boundary_main(argv):
    """Entry of a boundary child, started ONLY by ``capture`` through the
    owned runner. Reads and projects; starts no process; prints exactly ONE
    JSON value on stdout. A defect exits non-zero with its traceback on
    stderr, which the service reads as a boundary failure."""
    mode, payload = argv[0], json.loads(argv[1])
    try:
        if mode == MODE_PASS:
            value = _first_pass(payload)
        elif mode == MODE_PROJECT:
            value = _project(payload)
        else:
            raise ValueError("unknown boundary mode %r" % (mode,))
        out = {"ok": True, "value": value}
    except _Refused as exc:
        out = {"ok": False, "problem": exc.problem, "detail": exc.detail}
    print(json.dumps(out, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


# -- the owner: one long-lived instance, ONE slot ---------------------------

class _CleanupUnestablished(Exception):
    """The cleanup condition for an owned run was NOT established: the slot
    is kept. ``group`` is set only when the leader WAS reaped and the group
    id was observed to exist, the one case a later observation of ABSENCE
    can still settle; otherwise it is None and the slot is held for this
    owner's lifetime."""

    def __init__(self, detail, group=None):
        super(_CleanupUnestablished, self).__init__(detail)
        self.detail = detail
        self.group = group


def _group_absent(group, window=0.0):
    """Existence-only observations of a group whose leader was REAPED, for
    at most ``window`` seconds: True at the first observation of ABSENCE
    (``ESRCH``). Signal 0 sends nothing. It proves ABSENCE only, never
    presence or ownership: after the reap the id may have been reused, so an
    id that exists, a permission error and any other outcome are all
    UNKNOWN, and False is returned when the window ends without absence."""
    deadline = time.monotonic() + window
    while True:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(ABSENCE_POLL_SECONDS)


def _cleanup_condition(result):
    """The cleanup condition for ONE owned run, from the runner's own report
    (``herdr.runtime._bounded_command``), established BEFORE the run's
    outcome is used. Satisfied ONLY by:

    - the runner's EXACT native not-started report, ``{"started": False,
      "error": <str>}`` and nothing else: its ``Popen`` raised, so no
      process was created and nothing is owned (``started`` False beside
      any other key, a ``process_group`` included, is CONTRADICTORY and
      unestablished); or
    - a REAPED leader (``returncode`` an int: the runner's ``proc.wait``
      succeeded AFTER its existing pre-reap group SIGKILL) whose process
      group (``process_group``, the leader's pid) is then observed ABSENT
      within ``ABSENCE_WINDOW_SECONDS``.

    Everything else is UNESTABLISHED and fails CLOSED: a result that is not
    a mapping, a missing or malformed ``started``, a missing or malformed
    group, an UNREAPED leader (the runner's 1 s reap wait expired; a probe
    would only see the leader's own zombie, so none is taken), and a group
    id that exists or cannot be observed.

    The observation is an INSTANT, not a quiescence guarantee; nothing is
    signalled after the reap; a descendant that detached itself (``setsid``,
    or ``setpgid`` into another group) is unreachable through the group and
    is NOT covered; a member in an uninterruptible kernel read may persist,
    and then the group exists and the slot is held."""
    if type(result) is not dict:
        raise _CleanupUnestablished(
            "the runner returned no result mapping: cleanup unknown")
    started = result.get("started")
    if started is False and set(result) == {"started", "error"} and type(
            result["error"]) is str:
        return                  # Popen raised: no process was created
    if started is not True:
        raise _CleanupUnestablished(
            "the runner's report is not its native started or not-started"
            " shape (started %r): cleanup unknown" % (started,))
    group = result.get("process_group")
    if type(group) is not int or group <= 1:
        raise _CleanupUnestablished(
            "the runner did not name the process group it started:"
            " cleanup unknown")
    code = result.get("returncode")
    if type(code) is not int:
        raise _CleanupUnestablished(
            "the leader of process group %d was not reaped within the"
            " runner's wait, so its group cannot be observed: cleanup"
            " unknown" % group)
    if not _group_absent(group, ABSENCE_WINDOW_SECONDS):
        raise _CleanupUnestablished(
            "process group %d was not observed absent after its leader was"
            " reaped: cleanup unknown (the id may be ours or reused; nothing"
            " is signalled)" % group, group)


def _owned_run(argv):
    """The ONE place this module starts a process: the engine's owned runner
    (own group, stdin closed, read cap, group SIGKILL at the deadline before
    the leader is reaped), followed by the cleanup condition for that
    group. Called only by the owner's worker, never inside a boundary
    child."""
    result = engine_runtime._bounded_command(list(argv))
    _cleanup_condition(result)
    return result


def _boundary(mode, payload):
    """Run one boundary child and return its value, or raise a refusal."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise _Refused(PROBLEM_INPUT_TOO_LARGE,
                       "the %s payload exceeds %d bytes; nothing was started"
                       % (mode, MAX_PAYLOAD_BYTES))
    result = _owned_run([
        sys.executable, "-I", "-B", "-c", _BOUNDARY_CODE, _REPO_ROOT, mode,
        encoded])
    if type(result) is not dict or result.get("started") is not True or (
            result.get("error") is not None):
        raise _Refused(PROBLEM_BOUNDARY_FAILED,
                       "%s boundary was not started" % mode)
    if result.get("timed_out") is not False:
        raise _Refused(PROBLEM_BOUNDARY_TIMED_OUT,
                       "%s boundary did not finish within its deadline; its"
                       " process group was killed" % mode)
    if result.get("stdout_truncated_at_read") is not False:
        raise _Refused(PROBLEM_BOUNDARY_FAILED,
                       "%s boundary output exceeded its read cap" % mode)
    code = result.get("returncode")
    if type(code) is not int or code != 0:
        raise _Refused(PROBLEM_BOUNDARY_FAILED,
                       "%s boundary exited %r" % (mode, code))
    try:
        out = fileread.load_bounded_json(
            str(result.get("stdout")).encode("utf-8"))
    except (fileread.JsonRefused, UnicodeError):
        out = None
    if type(out) is dict and out.get("ok") is True and set(out) == {
            "ok", "value"}:
        return out["value"]
    if type(out) is dict and out.get("ok") is False and set(out) == {
            "ok", "problem", "detail"} and out["problem"] in _CHILD_PROBLEMS:
        detail = out["detail"]
        raise _Refused(out["problem"],
                       detail if type(detail) is str else None)
    raise _Refused(PROBLEM_BOUNDARY_FAILED,
                   "%s boundary output is not one boundary result" % mode)


def _bounded_inputs(herd_roots, listing_argv, discovered=()):
    """Shape AND length checks, before any serialization or process start
    (§5 of the contract); every filesystem check runs inside a boundary.
    Returns ``(registered, discovered_kept, over_capacity, argv)``: at most
    ``MAX_HERD_ROOTS`` roots are captured; registered roots first, then the
    discovered roots in the given order; the rest are OVER CAPACITY,
    reported and never captured (no silent truncation)."""
    if type(herd_roots) not in (list, tuple) or type(discovered) not in (
            list, tuple) or len(herd_roots) > MAX_HERD_ROOTS or not (
                1 <= len(herd_roots) + len(discovered)) or any(
                type(root) is not str or not root or "\x00" in root
                for root in list(herd_roots) + list(discovered)):
        raise _Refused(PROBLEM_ROOTS_INVALID,
                       "1..%d registered herd roots are required"
                       % MAX_HERD_ROOTS)
    if any(len(root) > MAX_ROOT_PATH_CHARS
           for root in list(herd_roots) + list(discovered)) or any(
            len(item) > MAX_ARGV_ELEMENT_CHARS for item in listing_argv):
        raise _Refused(PROBLEM_INPUT_TOO_LARGE,
                       "a herd root or listing argument exceeds its length"
                       " bound; nothing was started")
    registered = list(herd_roots)
    rest = []
    for root in discovered:
        if root not in registered and root not in rest:
            rest.append(root)
    capacity = MAX_HERD_ROOTS - len(registered)
    return registered, rest[:capacity], rest[capacity:], list(listing_argv)


def _discovered_accounts(value, roots, discovered):
    """A boundary child's first-pass value for a request WITH discovered
    roots, checked: one account per requested root, in order, each either
    digests or one of the refusals a child may report. ``None`` otherwise."""
    if type(value) is not dict or set(value) != {"digests", "discovered"} or (
            type(value["digests"]) is not list) or len(
                value["digests"]) != len(roots) or type(
                    value["discovered"]) is not list or len(
                        value["discovered"]) != len(discovered):
        return None
    for account, root in zip(value["discovered"], discovered):
        if type(account) is not dict or account.get("root") != root:
            return None
        if set(account) == {"root", "digests"}:
            if type(account["digests"]) is not list or len(
                    account["digests"]) != len(STATE_FILES):
                return None
        elif set(account) != {"root", "problem", "detail"} or (
                account["problem"] not in _CHILD_PROBLEMS):
            return None
    return value


def _require_chains(chains):
    """A chain request's argument, checked before anything starts: None, or
    ``{"expected_reviews": {root: task_id}}`` with at most
    ``MAX_HERD_ROOTS`` entries of bounded, non-empty strings."""
    if chains is None:
        return None
    if type(chains) is not dict or set(chains) != {"expected_reviews"}:
        raise TypeError("chains must be None or {'expected_reviews': {...}}")
    expected = chains["expected_reviews"]
    if type(expected) is not dict or len(expected) > MAX_HERD_ROOTS or any(
            type(k) is not str or type(v) is not str or not k or not v
            or "\x00" in k or "\x00" in v or len(k) > MAX_ROOT_PATH_CHARS
            or len(v) > snapshot_contract.MAX_SNAPSHOT_TEXT_CHARS
            for k, v in expected.items()):
        raise TypeError("expected_reviews must map bounded root paths to"
                        " task ids")
    return chains


def _chain_accounts(value, roots, discovered):
    """A boundary child's first-pass value for a CHAIN request, checked as
    data only: four digests per strict root, one account per discovered
    root in order, and one account per attempted child root, each either
    digests or one of the refusals a child may report. ``None`` otherwise."""
    if type(value) is not dict or set(value) != {
            "digests", "discovered", "children", "child_over_capacity"}:
        return None
    if type(value["digests"]) is not list or len(value["digests"]) != len(
            roots) or any(type(d) is not list or len(d) != len(
                STATE_FILES_V2) for d in value["digests"]):
        return None
    if type(value["discovered"]) is not list or len(
            value["discovered"]) != len(discovered) or any(
                type(a) is not dict or a.get("root") != root
                for a, root in zip(value["discovered"], discovered)):
        return None
    if type(value["children"]) is not list or type(
            value["child_over_capacity"]) is not list or len(
                value["children"]) > MAX_HERD_ROOTS or any(
                    type(r) is not str for r in value["child_over_capacity"]):
        return None
    for account in value["discovered"] + value["children"]:
        if type(account) is not dict or type(account.get("root")) is not str:
            return None
        if set(account) == {"root", "digests"}:
            if type(account["digests"]) is not list or len(
                    account["digests"]) != len(STATE_FILES_V2):
                return None
        elif set(account) != {"root", "problem", "detail"} or (
                account["problem"] not in _CHILD_PROBLEMS
                | {PROBLEM_INPUT_TOO_LARGE}):
            return None
    return value


def _chain_plan(value, roots, over_capacity, account, expected_reviews):
    """``(captured, digests, projection_chains)`` from a checked chain
    first pass, and the root account filled in (data only)."""
    available = [a for a in value["discovered"] if "digests" in a]
    children = [a for a in value["children"] if "digests" in a]
    captured = (list(roots) + [a["root"] for a in available]
                + [a["root"] for a in children])
    digests = (value["digests"] + [a["digests"] for a in available]
               + [a["digests"] for a in children])
    tiers = ([chain_contract.TIER_REGISTERED] * len(roots)
             + [chain_contract.TIER_DISCOVERED] * len(available)
             + [chain_contract.TIER_CHILD] * len(children))
    unavailable = [dict(a, tier=chain_contract.TIER_DISCOVERED)
                   for a in value["discovered"] if "digests" not in a] + [
        dict(a, tier=chain_contract.TIER_CHILD)
        for a in value["children"] if "digests" not in a]
    over = [{"root": root, "tier": chain_contract.TIER_DISCOVERED}
            for root in over_capacity] + [
        {"root": root, "tier": chain_contract.TIER_CHILD}
        for root in value["child_over_capacity"]]
    account["captured"] = captured
    account["unavailable"] = [
        {"root": a["root"], "problem": a["problem"], "detail": a["detail"]}
        for a in unavailable]
    account["children"] = {
        "recorded": [a["root"] for a in value["children"]]
        + list(value["child_over_capacity"]),
        "captured": [a["root"] for a in children],
        "unavailable": [a["root"] for a in value["children"]
                        if "digests" not in a],
        "over_capacity": list(value["child_over_capacity"])}
    return captured, digests, {"tiers": tiers,
                               "expected_reviews": dict(expected_reviews),
                               "unavailable": unavailable,
                               "over_capacity": over}


def _stable_names_accepted(value, stable_names):
    """The owner's own check of a child's ``native_cwd``: only requested
    names, each a list of bounded optional texts, at most one per listed
    agent."""
    if type(value) is not dict or not set(value) <= set(stable_names):
        return False
    for rows in value.values():
        if type(rows) is not list or not 1 <= len(rows) <= (
                snapshot_contract.MAX_SNAPSHOT_AGENTS):
            return False
        for cwd in rows:
            try:
                snapshot_contract.require_text(cwd, "native_cwd", True)
            except snapshot_contract.ContractError:
                return False
    return True


def require_stable_names(stable_names):
    """``request``'s check of ``stable_names``: None, or a list of distinct
    bounded agent names within ``MAX_STABLE_NAMES`` and
    ``MAX_STABLE_NAMES_BYTES``. Raises ``ValueError``."""
    if stable_names is None:
        return None
    if type(stable_names) is not list or len(stable_names) > (
            MAX_STABLE_NAMES) or len(set(stable_names)) != len(stable_names):
        raise ValueError("stable_names must be a list of at most %d distinct"
                         " names" % MAX_STABLE_NAMES)
    for name in stable_names:
        try:
            snapshot_contract.require_text(name, "stable_names")
        except snapshot_contract.ContractError:
            raise ValueError("stable_names holds a name that is not a"
                             " bounded text")
    if len(json.dumps(stable_names).encode("utf-8")) > MAX_STABLE_NAMES_BYTES:
        raise ValueError("stable_names exceeds %d serialized bytes"
                         % MAX_STABLE_NAMES_BYTES)
    return list(stable_names)


def _capture_work(roots, argv, observed_at, phase, discovered=(),
                  over_capacity=(), chains=None, stable_names=None):
    """The owner's worker: the fixed sequence of owned runs, each confirmed
    quiescent before the next starts. Returns a ``Capture``; raises
    ``_CleanupUnestablished`` when a group cannot be shown empty. With
    discovered roots, the unavailable ones are dropped after the first pass
    and the rest are captured with the registered roots (see
    ``_first_pass``); the ``Capture`` then carries the root account. A
    CHAIN request (``chains``, query contract version 2) runs the same three
    owned runs with the chain passes (``_first_pass_chains``,
    ``_project_chains``) and carries the chain observation as well."""
    account = None
    if discovered or over_capacity or chains is not None:
        account = {"registered": list(roots),
                   "discovered": list(discovered) + list(over_capacity),
                   "captured": None, "unavailable": [],
                   "over_capacity": list(over_capacity)}
    projection_chains = None
    try:
        captured, before = roots, None
        if chains is not None:
            phase("first pass")
            value = _chain_accounts(_boundary(MODE_PASS, {
                "roots": roots, "discovered": list(discovered),
                "chains": True,
                "capacity": MAX_HERD_ROOTS - len(roots) - len(discovered)}),
                roots, discovered)
            if value is None:
                raise _Refused(PROBLEM_BOUNDARY_FAILED,
                               "chain first pass value is not one account"
                               " per root")
            captured, before, projection_chains = _chain_plan(
                value, roots, over_capacity, account,
                chains["expected_reviews"])
            if not captured:
                return Capture(None, PROBLEM_NO_ROOT_OBSERVABLE,
                               "no registered, discovered or recorded child"
                               " root could be observed", observed_at,
                               account)
        elif discovered:
            phase("first pass")
            value = _discovered_accounts(_boundary(MODE_PASS, {
                "roots": roots, "discovered": list(discovered)}), roots,
                discovered)
            if value is None:
                raise _Refused(PROBLEM_BOUNDARY_FAILED,
                               "first pass value is not one account per"
                               " discovered root")
            available = [a for a in value["discovered"] if "digests" in a]
            account["unavailable"] = [a for a in value["discovered"]
                                      if "digests" not in a]
            captured = list(roots) + [a["root"] for a in available]
            before = value["digests"] + [a["digests"] for a in available]
            account["captured"] = captured
            if not captured:
                return Capture(None, PROBLEM_NO_ROOT_OBSERVABLE,
                               "no registered or discovered root could be"
                               " observed", observed_at, account)
        elif not roots:
            account["captured"] = []
            return Capture(None, PROBLEM_NO_ROOT_OBSERVABLE,
                           "every discovered root is over capacity",
                           observed_at, account)
        else:
            phase("first pass")
            before = _boundary(MODE_PASS, {"roots": roots})
            if account is not None:
                account["captured"] = list(roots)
        phase("listing")
        listing = _listing_stdout(_owned_run(argv))
        phase("projection")
        payload = {"roots": captured, "digests": before, "listing": listing,
                   "observed_at": observed_at}
        if projection_chains is not None:
            payload["chains"] = projection_chains
        if stable_names is not None:
            payload["stable_names"] = list(stable_names)
        snapshot = _boundary(MODE_PROJECT, payload)
    except _Refused as exc:
        return Capture(None, exc.problem, exc.detail, observed_at, account)
    native_cwd = None
    if stable_names is not None:
        # The owner's own acceptance of the requested stable field.
        if type(snapshot) is not dict or not _stable_names_accepted(
                snapshot.get("native_cwd"), stable_names):
            return Capture(None, PROBLEM_SELF_CHECK,
                           "the boundary value is not this observation",
                           observed_at, account)
        native_cwd = snapshot.pop("native_cwd")
        if chains is None:
            if set(snapshot) != {"snapshot"}:
                return Capture(None, PROBLEM_SELF_CHECK,
                               "the boundary value is not this observation",
                               observed_at, account)
            snapshot = snapshot["snapshot"]
    chain_observation = None
    if chains is not None:
        # The owner's own acceptance of the chain value: the same rules.
        if type(snapshot) is not dict or set(snapshot) != {
                "snapshot", "chain_observation"}:
            return Capture(None, PROBLEM_SELF_CHECK,
                           "the boundary value is not this observation",
                           observed_at, account)
        snapshot, chain_observation = (snapshot["snapshot"],
                                       snapshot["chain_observation"])
        chain_reading = chain_contract.read_chain_observation(
            chain_observation, observed_at)
        if (chain_reading.health != snapshot_contract.HEALTH_FRESH
                or chain_observation.get("observed_at") != observed_at
                or chain_observation.get("producer") != PRODUCER):
            return Capture(None, PROBLEM_SELF_CHECK,
                           "the boundary value is not this observation",
                           observed_at, account)
    # The owner's own acceptance of a child's value: this producer, its own
    # observation time, and fresh by the contract itself.
    reading = snapshot_contract.read_runtime_snapshot(snapshot, observed_at)
    if (type(snapshot) is not dict
            or reading.health != snapshot_contract.HEALTH_FRESH
            or snapshot.get("observed_at") != observed_at
            or snapshot.get("producer") != PRODUCER):
        return Capture(None, PROBLEM_SELF_CHECK,
                       "the boundary value is not this observation",
                       observed_at, account)
    return Capture(snapshot, None, None, observed_at, account,
                   chain_observation, native_cwd)


#: ``diagnostics()["work"]``: the owner's honest account of owned work.
WORK_SETTLED = "settled"        # slot free: the cleanup condition held
WORK_PENDING = "pending"        # a worker is still running its capture
WORK_UNCERTAIN = "uncertain"    # the worker ended; cleanup NOT established


def _await_worker(worker, ended, timeout):
    """Wait at most ``timeout`` seconds (``None``: no bound) for an owner's
    worker to END, and return whether its thread is gone. The RUN is awaited
    on ``ended``, NEVER by ``Thread.join``: on CPython with the bpo-45274
    join handling (seen on 3.9.25 to 3.12), an exception interrupting a join
    of a LIVE thread marks it stopped and drops it from interpreter-exit
    joining, so the process would abandon its owned work at exit. ``ended``
    is the run's last act (owned work and its kill/reap are over), so the
    join that follows waits for the thread's own teardown only."""
    deadline = None if timeout is None else time.monotonic() + timeout
    if not ended.wait(timeout):
        return False
    worker.join(None if deadline is None
                else max(0.0, deadline - time.monotonic()))
    return not worker.is_alive()


class SnapshotService(object):
    """THE supported owner: ONE long-lived instance with ONE in-flight
    capture slot (see "The owner and its caller contract").

    ``request(wait_seconds)`` returns within about ``wait_seconds`` with a
    snapshot or a refusal; it never queues and never starts a second capture
    while the slot is held. The slot is taken BEFORE anything starts and is
    released ONLY when the cleanup condition (``_cleanup_condition``) holds
    for every run the capture started: by the worker as it ends, or, when
    the only open question is a reaped run's group that was observed to
    exist, by ONE later existence-only observation of that group's ABSENCE,
    when the owner is next asked (never a signal, never a restart).
    Otherwise the slot stays held for this owner's lifetime and every later
    request is refused with the occupied-slot code. The slot's state lives
    in memory and in ``diagnostics()`` only; nothing is written.

    LIFETIME. Request return is not cleanup. ``shutdown()`` makes the owner
    refuse all NEW work (``capture_owner_shut_down``) and KEEPS
    responsibility for existing work: it neither waits for nor abandons the
    worker, and ``diagnostics()`` keeps reporting it as ``pending`` (still
    running) or ``uncertain`` (ended without its cleanup established), never
    as done. ``settle(timeout)`` waits for the worker to END (``None``: no
    timeout, and then no time bound is claimed). The worker is NOT a daemon
    thread, so interpreter exit cannot silently kill it mid-run (a daemon
    thread dies at exit without running its ``finally``): a process exits
    only after its owners' workers have ended, which the runner bounds on
    its supported path and NOTHING bounds on a stalled exec or a kernel
    stall."""

    def __init__(self, herd_roots, clock, listing_argv):
        self._roots = herd_roots
        self._clock = clock
        self._argv = _require_argv(listing_argv)
        self._mutex = threading.Lock()
        self._slot = None
        self._worker = None
        self._ended = None
        self._accepting = True
        self._released = threading.Event()
        self._released.set()

    def diagnostics(self):
        """The owner, as data: whether it accepts work; the slot (free, or
        occupied since when, in which phase and why); and ``work``:
        ``settled``, ``pending`` or ``uncertain`` (``WORK_*``)."""
        with self._mutex:
            accepting = self._accepting
            slot = None if self._slot is None else dict(self._slot)
            running = self._worker_running()
        if slot is None:
            return {"accepting": accepting, "slot": "free",
                    "work": WORK_SETTLED}
        slot["held_seconds"] = round(time.monotonic() - slot.pop("since"), 3)
        slot.update(accepting=accepting, slot="occupied",
                    work=WORK_PENDING if running else WORK_UNCERTAIN)
        return slot

    def wait_released(self, timeout):
        """Wait at most ``timeout`` seconds for the slot to be released;
        True when it is free."""
        return self._released.wait(timeout)

    def shutdown(self):
        """Refuse all new work from now on; keep responsibility for existing
        work. Returns ``diagnostics()``: it does not wait."""
        with self._mutex:
            self._accepting = False
        return self.diagnostics()

    def _observe_open_question(self):
        """With the mutex held: when the worker has ENDED and the only open
        question is a reaped run's group that existed, ONE existence-only
        observation; its ABSENCE releases the slot. Nothing else does."""
        group = self._slot and self._slot.get("absence_settles_group")
        if group and _group_absent(group):
            self._slot = None
            self._released.set()

    def settle(self, timeout=None):
        """Wait for the current worker, if any, to END (at most ``timeout``
        seconds; ``None`` waits without a time bound), then return
        ``diagnostics()``. Its ``work`` says what is still owed: ending is
        not cleanup, and ``uncertain`` stays uncertain."""
        with self._mutex:
            worker, ended = self._worker, self._ended
        if worker is not None and worker.ident is not None:
            _await_worker(worker, ended, timeout)
        # (a worker that never started is not waited for: the hold stands)
        with self._mutex:
            self._observe_open_question()
        return self.diagnostics()

    def request(self, wait_seconds, discovered_roots=(), chains=None,
                stable_names=None):
        """One request; ``discovered_roots`` (canonical discovery, e.g.
        ``mission_sources.roster.discovery``) are captured with this owner's
        registered roots, TOLERANTLY and within ``MAX_HERD_ROOTS``, by the
        SAME slot: discovery never adds an owner or a worker. ``chains``
        (query contract version 2 only; None otherwise) makes it a CHAIN
        request: ``{"expected_reviews": {root: task_id}}``, the task each
        root's canonical review record must belong to (``review_record``).
        Same slot, same three owned runs. ``stable_names``
        (``require_stable_names``; None: none) asks the SAME projection for
        those names' native ``cwd`` (``Capture.native_cwd``)."""
        if type(wait_seconds) not in (int, float) or not (
                0 < wait_seconds <= MAX_WAIT_SECONDS):
            raise ValueError("wait_seconds must be in (0, %d]"
                             % MAX_WAIT_SECONDS)
        _require_chains(chains)
        stable_names = require_stable_names(stable_names)
        observed_at = self._clock()
        if type(observed_at) is not int or observed_at < 0:
            raise TypeError("clock must return a non-negative int")
        try:
            roots, discovered, over, argv = _bounded_inputs(
                self._roots, self._argv, discovered_roots)
        except _Refused as exc:
            return Capture(None, exc.problem, exc.detail, observed_at)
        outcome = {}
        with self._mutex:
            if not self._accepting:
                return Capture(None, PROBLEM_OWNER_SHUT_DOWN,
                               "this owner was shut down and takes no new"
                               " work; nothing was started", observed_at)
            self._observe_open_question()
            if self._slot is not None:
                return Capture(
                    None, PROBLEM_SLOT_OCCUPIED,
                    "a previous capture's owned work is outstanding (%s);"
                    " nothing was started" % self._slot["held_because"],
                    observed_at)
            self._slot = {"since": time.monotonic(), "phase": "starting",
                          "observed_at": observed_at,
                          "held_because": "a capture is in progress"}
            self._released.clear()
            ended = threading.Event()   # the run's last act (_await_worker)
            worker = threading.Thread(
                target=self._work,
                args=(ended, roots, argv, observed_at, outcome, discovered,
                      over, chains, stable_names),
                name="runtime-snapshot-owner", daemon=False)
            self._worker, self._ended = worker, ended
            try:
                worker.start()
            except BaseException:
                # An interrupted or failed start does not PROVE that no
                # thread began: the slot stays held, reported uncertain.
                self._slot["held_because"] = (
                    "the worker's start was interrupted or failed: whether"
                    " it began is unknown")
                raise
        if not _await_worker(worker, ended, wait_seconds):
            return Capture(
                None, PROBLEM_CAPTURE_PENDING,
                "the capture did not finish within the %s s wait; its owned"
                " work continues and holds the slot until its cleanup is"
                " established" % wait_seconds, observed_at)
        if "raised" in outcome:
            raise outcome["raised"]
        return outcome["capture"]

    def _phase(self, name):
        with self._mutex:
            if self._slot is not None:
                self._slot["phase"] = name

    def _worker_running(self):
        """With the mutex held: the current worker started and has not
        ended its run (``ended`` is the run's last act)."""
        return (self._worker is not None and self._worker.ident is not None
                and not self._ended.is_set())

    def _work(self, ended, roots, argv, observed_at, outcome, discovered=(),
              over_capacity=(), chains=None, stable_names=None):
        release, held_because = False, "a capture is in progress"
        settles = None
        try:
            outcome["capture"] = _capture_work(roots, argv, observed_at,
                                               self._phase, discovered,
                                               over_capacity, chains,
                                               stable_names)
            release = True
        except _CleanupUnestablished as exc:
            held_because = "cleanup unestablished: %s" % exc.detail
            settles = exc.group
            outcome["capture"] = Capture(None, PROBLEM_CLEANUP_UNESTABLISHED,
                                         exc.detail, observed_at)
        except BaseException as exc:                      # noqa: BLE001
            # A defect: whether owned work remains is unknown, so the slot
            # stays held (fail closed); the defect is re-raised to a waiter.
            held_because = "a defect in the capture (%s)" % type(exc).__name__
            outcome["raised"] = exc
        finally:
            try:
                with self._mutex:
                    if release:
                        self._slot = None
                        self._released.set()
                    elif self._slot is not None:
                        self._slot["held_because"] = held_because
                        self._slot["absence_settles_group"] = settles
            finally:
                ended.set()         # the run's LAST act; teardown follows


def capture(herd_roots, clock, listing_argv,
            wait_seconds=ONE_SHOT_WAIT_SECONDS):
    """THE ONE-SHOT path (``dirun snapshot``): one request on a FRESH owner,
    then that owner is shut down and SETTLED before this returns. A one-shot
    has no later caller whose responsiveness it must protect, so it WAITS
    for its own owned work to end rather than abandon it: after a request
    that did not finish within ``wait_seconds`` it waits further, WITHOUT a
    time bound (the runner bounds the supported path; a stalled exec or a
    kernel stall is not bounded by anything here). What the returned
    ``Capture`` reports afterwards:

    - a snapshot or a refusal, when the request finished within the wait;
    - ``capture_not_finished_within_wait``, when it did not and its owned
      work then ENDED with the cleanup condition established (its late
      value is never returned);
    - ``capture_cleanup_unestablished``, whenever the owner ends with its
      slot still held (cleanup NOT established: something may remain, and
      this is never reported as done).

    A fresh owner gets a fresh slot: repeated one-shot calls are NOT bounded
    by one slot between them."""
    owner = SnapshotService(herd_roots, clock, listing_argv)
    try:
        result = owner.request(wait_seconds)
    finally:
        # The one-shot lifecycle runs on every path, errors included.
        owner.shutdown()
        state = owner.settle(None)
    if state["work"] != WORK_SETTLED:
        detail = state.get("held_because")
        return Capture(None, PROBLEM_CLEANUP_UNESTABLISHED,
                       "the one-shot owner ended with its owned work %s: %s"
                       % (state["work"], detail), result.observed_at)
    return result
