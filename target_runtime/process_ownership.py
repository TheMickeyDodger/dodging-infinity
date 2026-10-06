"""I5-3: a component that starts a process owns its WHOLE TREE.

The specimen this generalises
=============================

During I1 a pty leader was killed and its MCP grandchildren survived.
A leader-only kill is not ownership: the component reported the process
gone while descendants of it were still running. So the reaping
primitive lives here, in production code, rather than as a helper
private to one test file — and the guarantee it makes is about the
GROUP, not the leader.

The safety rule that cost the most to learn
===========================================

An earlier version read the child's process-group id before `setsid`
had landed in the child, got the PARENT'S group back, and signalled it
— killing the caller's own shell. So within `reap_group` a group whose
                                  leader has not been verified is left
                                  unsignalled: `os.getpgid(pid) == pid` must
hold, meaning the pid really is a group leader of its own group. Within
this module an unverified group is never signalled; outside it, a
caller that signals a group itself has no protection from here.

What is proven, and what is not
===============================

`tests/test_ownership.py` builds a real leader with a real grandchild
and asserts, after `reap_group`, that the grandchild is gone — so the
difference between killing a leader and reaping a group is observable
in every run rather than asserted in prose. Outside that: a descendant
that has already left the group (by calling `setsid` itself) is not
reachable through the group, and this module does not claim it is.
"""

import collections
import errno
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import signal
import threading
import time
import weakref

# Task 8 R24-2: the ONE shared genuine-absence classifier (stdlib-only; the
# package's names resolve lazily, so this adds no provider to any closure).
from workflow_authority.atomic import READ_ABSENT, READ_BLOCK_BYTES, classify_missing

#: Verdicts from `reap_group`.
REAPED = "reaped"
ALREADY_GONE = "already_gone"
REFUSED_UNVERIFIED_GROUP = "refused_unverified_group"
#: The leader was signalled but its GROUP was not, because the
#: group could not be verified as the leader's own. Distinct from
#: #: REAPED, so within this vocabulary a one-process kill is not reported
#: as a reaped tree.
REAPED_LEADER_ONLY = "reaped_leader_only"

#: How long to wait for the group to disappear after SIGKILL before
#: reporting what was actually observed. This bounds a REAP, which is
#: local cleanup of a process this component started — it is not a
#: #: #: #: deadline on an engineering mission; its scope is bounded to local
#: cleanup, outside a mission's execution path.
REAP_SETTLE_SECONDS = 5.0
REAP_POLL_SECONDS = 0.02


def group_is_verified(pid):
    """Whether ``pid`` is the leader of its OWN process group, AND is
    not the caller's own group.

    Two guards, and the second was added after execution found the
    first insufficient. A pid whose `getpgid` is not itself belongs to
    somebody else's group — that was the original check. But a caller
    started with `start_new_session=True` is ITSELF a group leader, so
    the original check passed for `os.getpid()` and the reaper would
    have killed the caller's own group. That is the same shape as the
    bug that once killed a developer's shell, arriving from the
    opposite direction, and it appeared the moment the harness began
    starting its children in their own sessions.

    Within this module neither shape is ever signalled; outside it, a
    caller that signals a group itself has no protection from here.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        if pid == os.getpgrp():
            return False
        return os.getpgid(pid) == pid
    except OSError:
        return False


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except OSError as exc:
        if exc.errno in (errno.ESRCH,):
            return False
        if exc.errno in (errno.EPERM,):
            # Alive, and not ours to signal. # Reported as alive, so within this helper a reap that did
            # not happen is not recorded as one.
            return True
        return True
    return True


def _process_exists(pid):
    """Task 8 R22-1: whether the PROCESS ``pid`` exists — True, False, or
    None when the OS answer cannot be read. Signal 0: nothing is delivered;
    it asks the one question about the one pid a record named, and only
    ESRCH is read as gone."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        return None
    return True


def reap_group(leader_pid, settle_seconds=None, sleeper=None,
               clock=None):
    """Kill and reap the ENTIRE group led by ``leader_pid``.

    Returns ``(verdict, detail)``. Within this function a group is
    signalled only after `group_is_verified` holds for its leader;
    outside that, the call falls back to the single owned leader or
    refuses, as the body documents.
    """
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    settle = (
        REAP_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    )
    if not group_is_verified(leader_pid):
        # The group is NOT signalled: it is the caller's own group, or
        # somebody else's, and reaching it would touch processes this
        # component did not start.
        #
        # # The LEADER still is, when it is a plausible child pid, and the
        # reason is that refusing outright would LEAK it: a child that
        # has not called `setsid` shares the caller's group, so within
        # that case signalling that one pid is the only safe cleanup. An
        # earlier draft of this delegation refused instead, and an
        # I1-era guarantee test caught the leak immediately.
        #
        # The residual, in the same breath: this reaches ONE process.
        # # A descendant of an unverified leader sits outside every group
        # this function may signal, so the verdict is REAPED_LEADER_ONLY
        # rather than REAPED.
        if (
            isinstance(leader_pid, int)
            and not isinstance(leader_pid, bool)
            and leader_pid > 1
            and leader_pid != os.getpid()
            and leader_pid != os.getpgrp()
        ):
            try:
                os.kill(leader_pid, signal.SIGKILL)
            except OSError:
                pass
            _reap_leader(leader_pid)
            return REAPED_LEADER_ONLY, (
                "pid %d was signalled directly; its GROUP was not,"
                " because the group is not the leader's own, so a"
                " descendant outside this pid is not reached"
                % leader_pid
            )
        return REFUSED_UNVERIFIED_GROUP, (
            "pid %r is not a signallable owned leader, so nothing was"
            " signalled" % (leader_pid,)
        )
    try:
        os.killpg(leader_pid, signal.SIGKILL)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            _reap_leader(leader_pid)
            return ALREADY_GONE, None
        return REFUSED_UNVERIFIED_GROUP, (
            "could not signal group %d: %s" % (leader_pid, exc)
        )
    # The leader is collected INSIDE the settle loop, not once before
    # it. A killed leader this process forked becomes a ZOMBIE until
    # it is waited for, and `killpg(pgid, 0)` succeeds against a
    # zombie — so a single pre-loop wait raced, the zombie read as a
    # live member, and the reap reported failure over a tree that was
    # already gone. Found by execution: the grandchild had exited and
    # the group still reported alive for the full settle window.
    deadline = clock() + settle
    while clock() < deadline:
        _reap_leader(leader_pid)
        if not _group_alive(leader_pid):
            return REAPED, None
        sleeper(REAP_POLL_SECONDS)
    _reap_leader(leader_pid)
    if _group_alive(leader_pid):
        return REFUSED_UNVERIFIED_GROUP, (
            "group %d still has a live member %.1fs after SIGKILL;"
            " the tree is reported as NOT reaped rather than assumed"
            " gone" % (leader_pid, settle)
        )
    return REAPED, None


def _reap_leader(pid):
    """Collect the leader's exit status so it does not linger as a
    zombie. A pid this process did not fork raises ECHILD, which is
    not an error here: it means this process has no child to collect here.

    Task 8 R28-1: an OWNED spawn of this process is collected through its own
    handle (``_collect_leader``) — never out of band — so the handle records
    its true status and a held leader's pin is dropped exactly here."""
    leader = _owned_leader(pid)
    if leader is not None:
        try:
            _collect_leader(leader)
        except OSError:
            pass
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:
        return


# --------------------------------------------------------------------
# R-14 / E-3: THE OWNED-SPAWN CONSTRUCT
# --------------------------------------------------------------------
#
# Reaping a group is only half of ownership. # The other half is being able to PROVE, later and from a different
# process, that this component started the group, because an orphaned
# group whose leader has already died sits outside what
# `group_is_verified` can confirm — which is exactly the shape the
# leaked specimens took.
#
# # So a spawn through this construct RECORDS its group id in an owner
# LEDGER, and within `reap_owned` a group id the ledger does not name is
# refused. The ledger is recorded evidence, in the same sense the product
# ownership predicate uses: # its scope: a name, an alias, a command pattern or a start time sits
# outside what it accepts. An over-broad reap that killed by
# name pattern would be a worse defect than the leak it fixes.

LEDGER_FILE_NAME = "owned-process-groups.jsonl"

REFUSED_NOT_IN_LEDGER = "refused_not_in_owner_ledger"
#: Task 8 R27 (the owner-ledger readers): ``reap_owned``'s ledger gate could not
#: make its observation — the ledger is PRESENT but NOT OBSERVED. Never "not in
#: the ledger" (an observation that was not made is not absence), and nothing is
#: signalled from a failed proof: the zero-reap unavailable-evidence rule.
REFUSED_LEDGER_UNAVAILABLE = "refused_owner_ledger_unavailable"
#: Task 8 R28-1: the ledger names the group, a group by that NUMBER is alive,
#: and it is NOT proven the one the ledger recorded: THIS process does not hold
#: its recorded leader (its own spawn, uncollected), and the recorded leader is
#: not alive and corroborated by its owned root (its nonce; its start time now)
#: — reused, leaderless, or never bound to a root. Nothing is signalled:
#: unresolved, never settled.
REFUSED_CURRENT_GROUP_UNPROVEN = "refused_current_group_unproven"
#: Task 8 R28-1: the CURRENT-group proof could not be MADE — a kernel answer, a
#: corroboration record or the ledger's binding row could not be read. Never
#: read as proven, never as disproven. Nothing is signalled.
REFUSED_CURRENT_GROUP_UNAVAILABLE = "refused_current_group_proof_unavailable"


def ledger_path(directory=None):
    """The owner ledger this process writes to, or None when no ledger
    directory was PASSED.

    The environment is deliberately outside what this function reads. An ambient variable is
    exactly the wrong shape for an ownership record — anyone able to
    set it could point this component's reaper at a ledger it did not
    write, which is the ambient-authority version of the name-pattern
    trap. The static-containment rule for `target_runtime` forbids
    `os.environ` here for that family of reasons, and it caught the
    first draft of this function.

    A caller that passes no directory gets no `reap_owned` powers,
    which is the fail-closed direction.
    """
    if not directory:
        return None
    return os.path.join(directory, LEDGER_FILE_NAME)


#: The environment variable a spawned child carries, so a crash
#: between the pending record and the group record still leaves the
#: process attributable to a nonce this component generated.
NONCE_ENV = "DI_OWNED_PROCESS_NONCE"


def record_pending(nonce, label, directory=None):
    """Record the INTENT to spawn, before the group exists."""
    import json
    path = ledger_path(directory)
    if path is None:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "pending": nonce, "label": label,
            "recorded_by": os.getpid(), "recorded_at": time.time(),
        }) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _owner_ledger_text(directory):
    """Task 8 R27: the owner ledger's TEXT for its group and pending readers
    (``owned_groups``, ``pending_nonces``), in the THREE branches
    ``ledger_groups`` reads it in, never collapsed:

    - ``None`` — no ledger directory was passed, or the ledger is GENUINELY
      absent (the traversal, ``_observed_missing``): it names nothing, as
      before;
    - the text — READABLE;
    - ``ObservationUnavailable`` RAISED — PRESENT but NOT OBSERVED: not a
      regular file, larger than ``OWNER_LEDGER_BYTES``, unreadable, a link
      that does not resolve, not UTF-8 (R21-2, R24-2). Never read as naming
      nothing.

    Read through ``read_ownership_record`` (R26-1: ONE non-waiting open, the
    OPENED descriptor validated before any byte, bounded), so a FIFO
    substituted at the ledger is never waited on; a VALID link is followed,
    exactly as the plain ``open`` this replaces did."""
    path = ledger_path(directory)
    if path is None:
        return None
    try:
        raw = read_ownership_record(path, OWNER_LEDGER_BYTES)
    except FileNotFoundError:
        gap = _observed_missing(path, "the owner ledger")
        if gap is None:
            return None
        raise ObservationUnavailable([gap])
    except OSError as exc:
        raise ObservationUnavailable([_unavailable(path, "the owner ledger", exc)])
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ObservationUnavailable([_unavailable(path, "the owner ledger", exc)])


def _ledger_rows(text):
    """The ledger's rows: each line that is a JSON OBJECT. A line that is
    not one (a torn last append, any other value) names nothing — as
    ``ledger_groups`` reads it."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            yield row


def pending_nonces(directory=None):
    """Nonces recorded as PENDING for which no group id was later recorded
    in this ledger.

    A non-empty result means a spawn crashed inside the window between
    the pending record and the group record. It is REPORTED rather
    than swept, because what that case needs is evidence, not a
    broader kill.

    Task 8 R27: read through ``_owner_ledger_text`` — never waited on, never
    unbounded. A ledger PRESENT but NOT OBSERVED raises
    ``ObservationUnavailable``: a failed observation never becomes an EMPTY
    pending result. (Reader-contract coherence: its one consumer,
    ``sweep_owned``, is reached through the OPTIONAL ``install_exit_sweep``,
    which no application code installs.)
    """
    pending, resolved = [], set()
    for row in _ledger_rows(_owner_ledger_text(directory)):
        if isinstance(row.get("pending"), str):
            pending.append(row["pending"])
        if isinstance(row.get("nonce"), str):
            resolved.add(row["nonce"])
    return sorted(set(pending) - resolved)


def record_owned_group(pgid, label, directory=None, nonce=None):
    """Append one owned process group to the ledger.

    Appended BEFORE the group starts work, and flushed at once. The
    residual it bounds: a crash inside that window could leave a group
    outside this ledger.
    """
    import json
    path = ledger_path(directory)
    if path is None:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "pgid": pgid, "label": label, "nonce": nonce,
            "recorded_by": os.getpid(), "recorded_at": time.time(),
        }) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def owned_groups(directory=None):
    """Every process group id the ledger names, as a set of ints.

    Task 8 R27: read through ``_owner_ledger_text`` — never waited on, never
    unbounded; a ledger GENUINELY absent names nothing (``set()``, as before).
    A ledger PRESENT but NOT OBSERVED raises ``ObservationUnavailable`` and is
    never read as naming nothing: ``reap_owned``'s gate would refuse for a
    FALSE reason, and ``surviving_owned_groups`` report a FALSE "no
    survivor"."""
    found = set()
    for row in _ledger_rows(_owner_ledger_text(directory)):
        pgid = row.get("pgid")
        if isinstance(pgid, int) and not isinstance(pgid, bool):
            found.add(pgid)
    return found


# --------------------------------------------------------------------
# Task 8 R28-1: THE HELD LEADER — a group NUMBER stays this process's own
# until the group is decided
# --------------------------------------------------------------------
#
# A process-group id is the pid of the process that created the group, and a
# pid is not reused while its process exists — a ZOMBIE included, until its
# parent collects it. So while THIS process has not collected its own spawn's
# leader, no other process holds that number, and none can create a group by
# it: a group signalled by that number can only be the leader's own. The
# moment the leader is COLLECTED the number is released, and a live group by
# it may from then on be a different incarnation — the reuse R28-1 names.
#
# The construct. ``reap_owned`` signals a group whose leader this process HOLDS
# (``_leader_hold``: its own spawn, not collected) — or, for a spawn it does not
# hold, one whose recorded leader is ALIVE and corroborated by its owned root,
# the proof recovery uses (``_recorded_leader_proves``). And a production
# consumer (``verification.produce``, the role turn's runner) spawns with
# ``hold_leader=True``, so its handle's ``wait`` and ``poll`` — and so
# ``communicate`` — OBSERVE the leader's exit WITHOUT collecting it while the
# hold is ARMED; the consumer reaps, and then DISARMS (``disarm_hold``). A
# reap that signals collects the leader through the reaper (``_reap_leader``);
# after a REFUSED reap the leader stays uncollected while its number still
# protects a LIVE member of its group — no production path waits on it again
# — so a later reap, or recovery, that proves its ownership can still act on
# exactly that group; an exited leader with nothing alive under its number is
# collected at the disarm. A disarmed handle collects as any handle does.
#
# The exit is observed without collecting it by ``os.waitid(..., WNOWAIT)``
# where the platform has it (Linux), and otherwise by a kqueue NOTE_EXIT watch
# ARMED THE MOMENT ``Popen`` RETURNS (macOS, BSD), whose event carries the
# exit status (NOTE_EXITSTATUS). Observed on this platform: a watch armed on a
# leader that had ALREADY exited fires at once with a status that is not the
# leader's, so a watch that fires as it is armed is never trusted — the hold
# is then given up and the handle collects as before (said so, never assumed).
#
# THE SPAN (the proof-interval correction). The proof and the signal are made
# inside ONE critical section with respect to collection of that leader: the
# reap (and recovery's signal, for a leader this process registered) holds the
# hold's lock and then the handle's own collection lock, ``Popen._waitpid_lock``
# — under which CPython performs EVERY collection of the child through its
# handle (``_wait``/``_try_wait``, ``_internal_poll``) and this module's
# ``_collect_leader`` performs its own — from the proof to the signal, and
# releases both before anything collects. So no collection through the handle,
# from ANY thread of this process, can fall between the proof and the signal:
# by construction, not by which callers exist. The acquisition is BOUNDED
# (``_SPAN_LOCK_SECONDS``): a lock held past it — a wait in progress on another
# thread holds the collection lock for as long as the child lives — refuses,
# unresolved, rather than waiting. THE LOCK ORDER, everywhere here: hold lock ->
# collection lock -> registry lock; no path holds a later one while it acquires
# an earlier one (an armed wait that must give its hold up does so OUTSIDE the
# hold lock), so none can deadlock against ``_waitpid_lock``.
#
# Its limit, stated: a collector that BYPASSES the handle — a raw ``waitpid`` on
# the pid, ``waitpid(-1)``/``os.wait``, a kernel that collects children itself
# (``SA_NOCLDWAIT``, which Python cannot read) — takes no lock of this process
# and is not excluded by one. ``_leader_hold`` re-reads the kernel at the action
# boundary (the pid exists; on Linux, it is still this process's uncollected
# child) and refuses when SIGCHLD is ignored. A search of the product source
# finds no such collector (a search, not a proof: this module's own raw
# ``waitpid`` in ``_reap_leader`` is reached only for a pid this process did not
# register).

#: ``<sys/event.h>``'s NOTE_EXITSTATUS (``select`` does not export it): a
#: NOTE_EXIT event then carries the exit status in its data.
_NOTE_EXITSTATUS = 0x04000000

#: Every owned spawn of THIS process, by pid (weak: an abandoned handle is not
#: kept alive by this table), and the HELD ones not yet collected (strong:
#: their uncollected leader is the pin a later reap's proof rests on).
_OWNED = weakref.WeakValueDictionary()
_HELD = {}
_REGISTRY_LOCK = threading.Lock()
#: How long one held observation may hold the hold's lock (seconds).
_HOLD_SLICE = 0.05
#: The bound on acquiring a proof->signal span's locks (seconds): past it, the
#: reap refuses rather than waiting on an observation or collection of the leader
#: that another thread of this process holds.
_SPAN_LOCK_SECONDS = 2.0
#: The bound on ONE membership observation (seconds): the ``ps`` listing
#: ``disarm_hold`` reads inside the hold's lock. An observer that has not answered
#: by then is UNAVAILABLE — never "no live member". It bounds local cleanup of a
#: process this component started, the order of the reap's own settle bound
#: (``REAP_SETTLE_SECONDS``); it is no deadline on any turn.
_OBSERVER_SECONDS = 5.0
#: ... and on observing that observer's OWN process end once it is killed.
_OBSERVER_END_SECONDS = 1.0
#: The membership observer: every process's pid, group and state, ONE listing.
_MEMBERSHIP_ARGV = ("ps", "-A", "-o", "pid=,pgid=,stat=")

HOLD_HELD = "held"
HOLD_NOT_HELD = "not held"
HOLD_UNAVAILABLE = "unavailable"


class _StatusUnknown(Exception):
    """The leader's exit cannot be read without collecting it."""


class _LeaderHold(object):
    """The hold of ONE held spawn: whether its handle's waits are observed-only
    (``armed``), whether the leader was collected, its exit status as OBSERVED
    (never written to the handle's ``returncode``, so a later ordinary wait
    still truly collects), and the kqueue watch, where one is used."""

    def __init__(self):
        self.armed = True
        self.collected = False
        self.observed = None
        self.watch = None
        self.trusted = True
        self.lock = threading.Lock()


def _arm_exit_watch(pid):
    """macOS/BSD: ``(kqueue, trusted)`` — a NOTE_EXIT watch on ``pid``, armed
    NOW. A watch that fires as it is armed (the leader had already exited, or
    did so in that instant) is closed and returned untrusted: its status is not
    reliably the leader's."""
    import select
    queue = select.kqueue()
    try:
        event = select.kevent(
            pid, filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT | _NOTE_EXITSTATUS)
        fired = queue.control([event], 1, 0)
    except BaseException:
        queue.close()
        raise
    if fired:
        queue.close()
        return None, False
    return queue, True


def _observe_exit(process, hold, timeout):
    """The held leader's exit status, observed WITHOUT collecting it: an int;
    None while it still runs (``timeout`` elapsed — 0 polls); ``_StatusUnknown``
    when its exit cannot be read without collecting it. Call under
    ``hold.lock``."""
    if hold.observed is not None:
        return hold.observed
    if hasattr(os, "waitid"):
        flags = os.WEXITED | os.WNOWAIT
        if timeout is None:
            info = os.waitid(os.P_PID, process.pid, flags)
        else:
            deadline, delay = time.monotonic() + timeout, 0.0005
            while True:
                info = os.waitid(os.P_PID, process.pid, flags | os.WNOHANG)
                remaining = deadline - time.monotonic()
                if info is not None or remaining <= 0:
                    break
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.05)
        if info is None:
            return None
        hold.observed = (info.si_status if info.si_code == os.CLD_EXITED
                         else -info.si_status)
        return hold.observed
    if hold.watch is None:
        raise _StatusUnknown()
    events = hold.watch.control(None, 1, timeout)
    if not events:
        return None
    hold.watch.close()
    hold.watch = None
    hold.observed = os.waitstatus_to_exitcode(events[0].data)
    return hold.observed


def _hold_leader(process):
    """Arm the hold on ``process`` (a handle ``spawn_owned`` just created): its
    ``_wait`` and ``_internal_poll`` — the two routes by which CPython's
    ``wait``, ``poll``, ``communicate``, ``__del__`` and ``_cleanup`` collect a
    child — OBSERVE while armed, and defer to the handle's own otherwise. Set on
    the INSTANCE, so whatever class built the handle is kept. A platform with
    neither ``waitid`` nor kqueue gets no hold (the handle collects as before);
    so does a leader whose watch cannot be trusted, when it is first waited.

    A handle WITHOUT CPython's collection routes (``_wait``, ``_internal_poll``,
    ``_waitpid_lock`` — a stand-in that replaced ``subprocess.Popen``) gets no
    hold either, decided FIRST: it has none of those routes to hold, and no exit
    watch is armed on a pid that no child of this process need hold. It stays
    registered, and its reap takes the not-held proof."""
    if not all(hasattr(process, route)
               for route in ("_wait", "_internal_poll", "_waitpid_lock")):
        return
    hold = _LeaderHold()
    if not hasattr(os, "waitid"):
        try:
            hold.watch, hold.trusted = _arm_exit_watch(process.pid)
        except (ImportError, AttributeError, OSError):
            hold.watch, hold.trusted = None, False
    base_wait, base_poll = process._wait, process._internal_poll

    def _give_up(collect):
        hold.armed = False
        result = collect()
        if process.returncode is not None:
            _release(process)
        return result

    def _wait(timeout):
        # Observed in SLICES, the lock held for one slice at a time: a
        # concurrent poll waits at most one slice, and a second waiter sees the
        # status the first one observed instead of waiting on an event already
        # taken.
        if not hold.armed or hold.collected:
            return base_wait(timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            piece = _HOLD_SLICE if remaining is None else max(0.0, min(_HOLD_SLICE, remaining))
            unknown = False
            with hold.lock:
                try:
                    status = _observe_exit(process, hold, piece)
                except _StatusUnknown:
                    unknown = True
            if unknown:
                # given up OUTSIDE the hold's lock: a blocking base wait (which
                # holds the collection lock) never holds the hold's lock too
                return _give_up(lambda: base_wait(timeout))
            if status is not None:
                return status
            if remaining is not None and remaining <= piece:
                import subprocess
                raise subprocess.TimeoutExpired(process.args, timeout)

    def _internal_poll(*args, **kwargs):
        if not hold.armed or hold.collected:
            return base_poll(*args, **kwargs)
        unknown = False
        with hold.lock:
            try:
                return _observe_exit(process, hold, 0)
            except _StatusUnknown:
                unknown = True
            except OSError:
                return None
        if unknown:
            return _give_up(lambda: base_poll(*args, **kwargs))

    process._di_hold = hold
    process._wait = _wait
    process._internal_poll = _internal_poll
    with _REGISTRY_LOCK:
        _HELD[process.pid] = process


def _register_owned(process, directory, nonce, root, hold):
    """Record ``process`` as THIS process's own spawn for ``directory`` (the
    ledger it was recorded in) — the in-process identity ``_leader_hold``
    binds a reap to — and arm its hold when asked."""
    process._di_owner = (os.path.abspath(directory) if directory else None, nonce, root)
    with _REGISTRY_LOCK:
        _OWNED[process.pid] = process
    if hold:
        _hold_leader(process)


def _owned_leader(pid):
    with _REGISTRY_LOCK:
        return _HELD.get(pid) or _OWNED.get(pid)


def _release(process):
    """The held leader is collected: its watch closed, its pin dropped."""
    hold = getattr(process, "_di_hold", None)
    if hold is not None:
        hold.collected, hold.armed = True, False
        watch, hold.watch = hold.watch, None
        if watch is not None:
            watch.close()
    with _REGISTRY_LOCK:
        if _HELD.get(process.pid) is process:
            _HELD.pop(process.pid, None)


def _is_collected(process):
    hold = getattr(process, "_di_hold", None)
    return process.returncode is not None or (hold is not None and hold.collected)


def _collect_leader(process):
    """Collect ONE spawn of this process, never blocking; True once collected.
    A held one is collected HERE (its handle's own waits only observe while
    armed): its true status recorded on the handle, its pin dropped. Any other
    owned handle collects through its own ``poll``, which records its status."""
    if _is_collected(process):
        if process.returncode is not None:
            _release(process)
        return True
    if getattr(process, "_di_hold", None) is None:
        process.poll()
        return process.returncode is not None
    with process._waitpid_lock:
        try:
            pid, status = os.waitpid(process.pid, os.WNOHANG)
        except ChildProcessError:
            pid, status = process.pid, None     # collected outside this module
        if pid == 0:
            return False
        if process.returncode is None:
            if status is not None:
                process.returncode = os.waitstatus_to_exitcode(status)
            elif process._di_hold.observed is not None:
                process.returncode = process._di_hold.observed
    _release(process)
    return True


def _end_observer(observer):
    """End the membership observer ``_live_members_besides`` STARTED, and say
    what was DONE and what was OBSERVED — NEVER raising: a failure of its own
    cleanup is part of the truthful reason, never an exception that could escape
    the cleanup decision (and reach a caller that would read it as "nothing was
    started"). Only that process is addressed: ``Popen.kill`` polls first and
    signals only a child its handle has not collected, so a pid it signals is
    still that observer's own — and one already ended is not signalled at all,
    so a kill INVOKED is not a kill delivered. What is reported as observed is
    the wait's result alone: the observer seen to end within
    ``_OBSERVER_END_SECONDS``, with its status (which proves its end, not its
    cause), or its end UNPROVEN — its handle then left as it is, and said so."""
    import subprocess
    try:
        observer.kill()
        invoked = "a kill was invoked on the observer's own handle"
    except Exception as exc:                            # noqa: BLE001 - its kill: reported
        invoked = "a kill invoked on the observer's own handle FAILED (%s)" % (
            exc.__class__.__name__,)
    try:
        observer.wait(timeout=_OBSERVER_END_SECONDS)
    except subprocess.TimeoutExpired:
        return "%s; its end was NOT observed within %.1f s: UNPROVEN, its handle left" % (
            invoked, _OBSERVER_END_SECONDS)
    except Exception as exc:                            # noqa: BLE001 - its wait: reported
        return "%s; its end could NOT be observed (%s): UNPROVEN, its handle left" % (
            invoked, exc.__class__.__name__)
    return "%s; its end was observed (status %s)" % (invoked, observer.returncode)


def _live_members_besides(pgid, pid):
    """``(count, None)`` — how many LIVE processes (zombies excluded: no signal
    can reach them) other than ``pid`` are in group ``pgid`` now, ONE group asked
    about, read from ONE ``ps`` listing — or ``(None, why)`` when that cannot be
    established. "None" is a COUNT, never a status: a count is returned ONLY from
    a listing that is affirmatively valid and complete; anything else is
    UNAVAILABLE (Task 8 R28-1):
    - BOUNDED: the listing must be complete within ``_OBSERVER_SECONDS``; an
      observer that has not answered by then, cannot be started or read, exits
      non-zero, prints nothing or prints any diagnostic is unavailable. Its
      cleanup touches only the process the observer itself started
      (``_end_observer``), and NEVER raises: a cleanup step of its own that
      fails — the kill, the wait, a pipe that will not close — is reported in
      the UNAVAILABLE reason, never thrown at the cleanup decision;
    - STRICT: every row must be exactly ``pid pgid state``, ASCII, with integer
      ids and no pid listed twice; a row that does not parse is never skipped
      (a skip undercounts toward zero);
    - a POSITIVE RECEIPT: the listing must hold THIS process's own row, in its
      own group, and the observer's own row — a listing of everything holds
      both, so their absence means it is not that listing. (A receipt proves the
      listing is this host's own; it cannot prove that no row was omitted.)
    Used only to decide whether a held number still protects anything; nothing
    of the group is signalled from it."""
    import subprocess
    try:
        observer = subprocess.Popen(list(_MEMBERSHIP_ARGV), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
    except Exception as exc:                            # noqa: BLE001 - unread, not none
        return None, "the membership observer could not be started (%s)" % (
            exc.__class__.__name__,)
    unread = []
    try:
        listing, diagnostic = observer.communicate(timeout=_OBSERVER_SECONDS)
    except subprocess.TimeoutExpired:
        unread.append("the membership observer did not answer within %.1f s; %s" % (
            _OBSERVER_SECONDS, _end_observer(observer)))
    except Exception as exc:                            # noqa: BLE001 - unread, not none
        unread.append("the membership observer could not be read (%s); %s" % (
            exc.__class__.__name__, _end_observer(observer)))
    for name in ("stdout", "stderr"):
        stream = getattr(observer, name, None)
        if stream is None:
            continue
        try:
            stream.close()
        except Exception as exc:                        # noqa: BLE001 - its pipe: reported
            unread.append("the observer's %s did not close (%s)" % (
                name, exc.__class__.__name__))
    if unread:
        return None, "; ".join(unread)
    if observer.returncode != 0 or not listing or diagnostic:
        return None, ("the membership observer exited %s with %d byte(s) of listing and"
                      " %d of diagnostic" % (observer.returncode, len(listing or b""),
                                             len(diagnostic or b"")))
    try:
        rows = listing.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None, "the membership listing is not ASCII"
    me, mine, seen, count = os.getpid(), os.getpgrp(), {}, 0
    for number, line in enumerate(rows, 1):
        fields = line.split()
        try:
            member, group = int(fields[0]), int(fields[1])
        except (IndexError, ValueError):
            member = group = None
        if len(fields) != 3 or member is None or member in seen:
            return None, "the membership listing's row %d does not parse (or repeats a pid)" % (
                number,)
        seen[member] = group
        if group == pgid and member != pid and not fields[2].startswith("Z"):
            count += 1
    if seen.get(me) != mine or observer.pid not in seen:
        return None, ("the membership listing holds no row for this process in its own"
                      " group, or none for the observer: it is not a listing of every"
                      " process")
    return count, None


def disarm_hold(process):
    """Task 8 R28-1: the consumer's reap is DECIDED — ``process``'s handle waits
    and polls as any handle does from now on. A reap that signalled has already
    collected the leader. Otherwise the leader stays UNCOLLECTED — its number
    held, so a later reap or recovery that proves its ownership can still act —
    exactly while that number still protects something: a leader that has EXITED
    with NO live member left in its group (read FRESH; zombies are not members
    that a signal could reach) is collected here, since nothing remains under it
    to protect, and holding it would only keep a gone group looking unresolved.
    A leader still running, or whose group's membership cannot be read, stays
    held. A caller that later waits on it explicitly gives that pin up.

    BOUNDED: the hold's lock is acquired within ``_SPAN_LOCK_SECONDS`` and the
    membership read is bounded and strict (``_live_members_besides``), so no
    unresponsive or unreadable observer — nor anything else holding that lock —
    keeps this decision pending without bound. A decision that cannot be made is
    NO decision: nothing is signalled or collected, the leader stays HELD (its
    number pinned, its records retained for a later reap or recovery) and the
    lock is released. Returns the decision in words — "collected", or "kept
    held: <why>" — never a settlement; None for a handle with no hold."""
    hold = getattr(process, "_di_hold", None)
    if hold is None:
        return None
    hold.armed = False
    # The decision and the collection are ONE critical section under the hold's
    # lock (the collection lock is taken inside ``_collect_leader``: the one
    # order), so no span holding that lock sees them interleave.
    if not hold.lock.acquire(timeout=_SPAN_LOCK_SECONDS):
        return ("kept held: its hold's lock was not acquired within %.1f s (an"
                " observation or a proof->signal span of this leader is in progress)"
                % _SPAN_LOCK_SECONDS)
    try:
        if _is_collected(process):
            return "collected"
        try:
            status = _observe_exit(process, hold, 0)
        except (_StatusUnknown, OSError):
            return "kept held: its exit cannot be read without collecting it"
        if status is None:
            return "kept held: it is still running"
        members, why = _live_members_besides(process.pid, process.pid)
        if members is None:
            return "kept held: whether anything lives in its group is UNAVAILABLE (%s)" % why
        if members:
            return "kept held: %d live member(s) remain in its group" % members
        if _collect_leader(process):
            return "collected"
        return "kept held: it could not yet be collected"
    finally:
        hold.lock.release()


def _unpin(process):
    """Task 8 R28-1: ``process`` is not a spawn any consumer reaps (its parent's
    confirmation failed): its hold is disarmed and its STRONG pin dropped, so its
    handle's lifetime is its holder's again, as before the hold existed."""
    hold = getattr(process, "_di_hold", None)
    if hold is not None:
        hold.armed = False
    with _REGISTRY_LOCK:
        if _HELD.get(process.pid) is process:
            _HELD.pop(process.pid, None)


def exit_status_of(process):
    """Task 8 R28-1: ``process``'s exit status — its handle's return code once
    collected, else the status its hold OBSERVED; None while neither is known."""
    if process.returncode is not None:
        return process.returncode
    hold = getattr(process, "_di_hold", None)
    return hold.observed if hold is not None else None


def _span_locks(leader):
    """Task 8 R28-1: the locks a proof->signal span holds for ``leader`` (a handle
    this process registered, or None), in their ONE ORDER: the hold's lock (a
    held spawn's), then the handle's own collection lock (``Popen._waitpid_lock``:
    CPython's every collection of the child — ``_wait``/``_try_wait``,
    ``_internal_poll`` — runs under it, and so does ``_collect_leader``). THE LOCK
    ORDER, everywhere in this module: hold lock -> collection lock -> registry
    lock; no path holds a later one while it acquires an earlier one."""
    if leader is None:
        return []
    locks = []
    hold = getattr(leader, "_di_hold", None)
    if hold is not None:
        locks.append(hold.lock)
    collection = getattr(leader, "_waitpid_lock", None)
    if collection is not None:
        locks.append(collection)
    return locks


def _leader_hold(pgid, directory, leader=None):
    """Task 8 R28-1: ``(state, why)`` — whether THIS process holds the leader of
    group ``pgid`` for ``directory``'s ledger, read FRESH at the action boundary.
    ``leader`` is the handle a span LOCKED for it: it must still be this
    process's registration for the number (else HOLD_NOT_HELD).

    - ``HOLD_HELD``: ``pgid`` is this process's own spawn for that ledger
      (``spawn_owned`` recorded it there), not collected, and the kernel agrees
      — a process holds the pid (signal 0; nothing is delivered) and, where the
      platform can say (``waitid``), it is still this process's uncollected
      child;
    - ``HOLD_NOT_HELD`` (with why): never this process's spawn here; already
      collected; spawned for ANOTHER owner scope; or the kernel contradicts the
      record (no process holds the pid, it is held by a process this one may
      not signal, or it is no longer this process's child);
    - ``HOLD_UNAVAILABLE`` (with why): the kernel's answer could not be read, or
      SIGCHLD is ignored here (the kernel may then collect a child itself)."""
    current = _owned_leader(pgid)
    if leader is None:
        leader = current
    elif current is not leader:
        return HOLD_NOT_HELD, (
            "the leader locked for group %d is no longer this process's registration"
            " for that number" % pgid)
    if leader is None or _is_collected(leader):
        return HOLD_NOT_HELD, (
            "this process holds no uncollected leader of group %d (it was never this"
            " process's spawn here, or it was already collected)" % pgid)
    owner = getattr(leader, "_di_owner", (None,))[0]
    if owner is None or not directory or owner != os.path.abspath(directory):
        return HOLD_NOT_HELD, (
            "the leader this process holds under number %d was spawned for another"
            " owner scope" % pgid)
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        return HOLD_UNAVAILABLE, (
            "SIGCHLD is ignored in this process, so the kernel may collect a child"
            " itself: whether the leader of group %d is held cannot be established" % pgid)
    try:
        os.kill(pgid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return HOLD_NOT_HELD, (
                "no process holds pid %d: its leader was collected outside this"
                " module" % pgid)
        if exc.errno == errno.EPERM:
            return HOLD_NOT_HELD, (
                "pid %d is held by a process this one may not signal" % pgid)
        return HOLD_UNAVAILABLE, (
            "whether a process holds pid %d could not be read (%s)"
            % (pgid, exc.__class__.__name__))
    if hasattr(os, "waitid"):
        try:
            os.waitid(os.P_PID, pgid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return HOLD_NOT_HELD, (
                "the kernel reports pid %d is not this process's uncollected child" % pgid)
        except OSError as exc:
            return HOLD_UNAVAILABLE, (
                "whether pid %d is this process's uncollected child could not be read"
                " (%s)" % (pgid, exc.__class__.__name__))
    return HOLD_HELD, None


def _recorded_leader_proves(pgid, directory):
    """Task 8 R28-1: whether the LIVE group ``pgid`` is the one ``directory``'s
    ledger recorded, by the proof recovery uses: a ledger row binds the number to
    a spawn's nonce, that spawn's owned root lies beside the ledger (both
    production spawns, and the post-harness pin, record both under one scope),
    and ``group_is_ours`` corroborates it — its nonce names the root, and its
    leader's start time NOW equals the recorded one. ``(True, None)``;
    ``(False, why)`` — no binding row, no root, or the proof contradicts it
    (reused, leaderless, unnonced, a cut start record, a gone group);
    ``(None, why)`` — the ledger, a root or a corroboration record could not be
    read, or the live leader's start time could not. Never signals."""
    groups, gap = ledger_groups(directory)
    if gap is not None:
        return None, "its owner ledger cannot be read (%s)" % gap[1]
    nonces = sorted(nonce for nonce, pgids in groups.items() if pgid in pgids)
    if not nonces:
        return False, "no ledger row binds group %d to a spawn's owned root" % pgid
    reasons = []
    for nonce in nonces:
        root = os.path.join(owned_root_base(directory), nonce)
        try:
            mode = os.lstat(root).st_mode
        except FileNotFoundError:
            gap = _observed_missing(root, "the owned root")
            if gap is not None:
                return None, gap[1]
            reasons.append("its spawn %s has no owned root beside the ledger" % nonce)
            continue
        except OSError as exc:
            return None, _unavailable(root, "the owned root", exc)[1]
        if not stat.S_ISDIR(mode):
            reasons.append("its spawn %s's owned root is not a directory" % nonce)
            continue
        try:
            ours, why = group_is_ours(root)
        except (OSError, UnicodeDecodeError) as exc:
            return None, "%s (%s)" % (UNCORROBORATED_UNAVAILABLE, exc.__class__.__name__)
        if ours == pgid:
            return True, None
        if why in LEADER_UNAVAILABLE_REASONS:
            return None, why
        reasons.append(why or "its recorded group is gone")
    return False, "; ".join(reasons)


def spawn_owned(argv, label, directory=None,
                owned_root_base_dir=None, hold_leader=False, **popen_kwargs):
    """Start a subprocess in its OWN session and record the group.

    `start_new_session=True` makes the child a group leader, so its
    whole tree is reachable through one group id, which is the reach a
    leader-only kill lacks.

    R-16 F-2, the window this closes: a group id does not EXIST until `Popen` returns, so a record written
    afterwards leaves a window, bounded by that call, in which a crash
    orphans a process no durable evidence names. That shape
    was present in the first version of this function and was named as
    a candidate mechanism for the post-harness leak, so it is closed
    rather than argued about.

    The close: a PENDING record carrying a fresh nonce is written
    BEFORE the spawn, and the nonce is handed to the child in its
    environment. A crash inside the window leaves a durable record
    naming that nonce, and `pending_nonces` reports it — so the orphan
    stays attributable through evidence THIS component generated and
    recorded, which is what separates it from matching on a name or a
    command string.

    Task 8 R28-1: every spawn is registered as THIS process's own for
    ``directory`` (``_leader_hold`` binds a reap to it). With ``hold_leader``
    its handle is HELD: its ``wait``, ``poll`` and ``communicate`` observe the
    leader's exit WITHOUT collecting it until the caller disarms the hold
    (``disarm_hold``) after its reap — so the reap acts while the leader's
    number is still this process's (see "THE HELD LEADER"). The observed exit
    status is ``exit_status_of(handle)``.
    """
    import subprocess
    if is_frozen(owned_root_base_dir):
        raise SpawnGated(
            "spawning is FROZEN (%s). The freeze is durable state read"
            " BEFORE this spawn, so it takes effect without this"
            " process having to receive a message — which is the"
            " property a queued stop instruction lacked."
            % (freeze_reason(owned_root_base_dir) or "no reason"
               " recorded",)
        )
    if spawning_is_gated(directory):
        raise SpawnGated(
            "spawning is gated for %s; the gate exists so a harness"
            " can stop emitting BEFORE its zero-survivor measurement,"
            " rather than racing its own output" % directory
        )
    nonce = "own-" + secrets.token_hex(8)
    record_pending(nonce, label, directory)
    # R-19 I-3: the OWNED ROOT is created BEFORE the spawn, so a
    # durable artifact naming this spawn exists even if the ledger is
    # later lost — which is what left four orphans in this increment
    # unattributable.
    root = create_owned_root(nonce, owned_root_base_dir)
    # R-27 S-2: CHILD-SIDE SELF-STAMPING, so the entity that survives
    # a parent crash is the one that recorded itself.
    #
    # The window R-27 found: `Popen` returns, the child is ALIVE, and
    # the parent dies before `record_owned_group` stamps the pgid.
    # That leaves a live child under an UNSTAMPED root, which recovery
    # correctly refuses to bind — so the orphan survives. Closing the
    # pre-`Popen` window did not close this one, and R-23's summary
    # that the leaks reduced to one inverted line is withdrawn.
    #
    # `preexec_fn` runs IN THE CHILD, after the fork and after
    # `start_new_session` has made it a group leader, and before
    # `exec`. # Stamping there means the child records its own pgid within the
    # fork, before the parent reaches its own write, and it needs no
    # cooperation from the program being executed — which an
    # environment-variable contract would, since an arbitrary binary has
    # no reason to read a tag and stamp itself.
    #
    # # It also needs no ambient read: within this function `os.environ`
    # is not consulted, so `ledger_path`'s objection and the static
    # containment rule both stand as written rather than needing an
    # exemption.
    #
    # The residual, in the same breath: `preexec_fn` runs after fork
    # in a process that has not yet exec'd, so a failure there fails
    # the spawn rather than silently skipping the stamp; and a child
    # that changes its own process group after exec moves outside the
    # group this stamp names.
    # R-28 T-3: the stamp happens in a child that has EXEC'D, via
    # `target_runtime.spawn_stamp`, rather than in a `preexec_fn`
    # callable running in the fork window. This repository runs
    # threads, and an arbitrary callable between fork and exec can
    # deadlock on a lock held by another thread at fork time — a live
    # hazard, not a residual. `start_new_session` stays: session
    # creation is handled by CPython itself and is not the risk.
    # The wrapper is invoked by ABSOLUTE FILE PATH rather than with
    # `-m`: a spawned child gets a fresh interpreter whose `sys.path`
    # need not contain this repository, and `-m` would then fail to
    # import. `spawn_stamp` deliberately imports only the standard
    # library so it can run as a plain script.
    argv = [
        sys.executable, _STAMP_WRAPPER, root, "--",
    ] + list(argv)
    popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)
    # Task 8 R28-1: registered — and, for ``hold_leader``, its hold ARMED — the
    # moment ``Popen`` returns, before anything else: the leader is then still
    # the stamping wrapper's interpreter, so the kqueue exit watch is armed while
    # it runs (see "THE HELD LEADER").
    # (Task 8 R28-1: any failure from here on means no consumer reaps this
    # spawn, so it is not held — ``_unpin`` — and its handle waits as any
    # handle does; it stays registered as this process's own. And the failure
    # CARRIES the started child (``started_process``), its type and message
    # unchanged, so no caller can read it as "nothing started".)
    try:
        _register_owned(proc, directory, nonce, root, hold_leader)
    except BaseException as exc:
        _unpin(proc)
        _carry_started(exc, proc)
        raise
    # The parent's records are now CONFIRMATION rather than the only
    # evidence: the child stamps its own root before it execs the real
    # command (and refuses to exec when it cannot), so a parent that dies
    # on the next line leaves a root the child stamps. (Task 8 R26:
    # ``Popen`` returns once the WRAPPER is exec'd, before its stamp is
    # known to be done; the two stamps are not ordered, and both use the
    # same validating writer.)
    try:
        record_owned_group(proc.pid, label, directory, nonce=nonce)
    except BaseException as exc:
        _unpin(proc)
        _carry_started(exc, proc)
        raise
    try:
        record_owned_root_group(root, proc.pid)
    except OSError as exc:
        # Task 8 R26: after ``Popen`` a stamp refusal is NEVER "no spawn" —
        # the child exists. Reported as started and UNRESOLVED; nothing is
        # signalled or removed here.
        _unpin(proc)
        raise SpawnUnconfirmed(proc, root, exc) from exc
    except BaseException as exc:
        _unpin(proc)
        _carry_started(exc, proc)
        raise
    return proc


#: Task 8 R28-1: the attribute on which a failure of ``spawn_owned`` raised AFTER
#: ``Popen`` returned CARRIES the started child — the exception's type and message
#: unchanged (``SpawnUnconfirmed`` carries it as ``process``).
STARTED_PROCESS_ATTRIBUTE = "di_started_process"


def _carry_started(exc, process):
    try:
        setattr(exc, STARTED_PROCESS_ATTRIBUTE, process)
    except Exception:                                   # noqa: BLE001 - it carries nothing
        pass


def started_process(exc):
    """Task 8 R28-1: the child ``spawn_owned`` STARTED before it raised ``exc`` —
    a process exists, so the failure is POST-SPAWN, never "nothing started" — or
    None when ``exc`` carries none (raised before ``Popen`` returned a process, or
    not by ``spawn_owned``)."""
    if isinstance(exc, SpawnUnconfirmed):
        return exc.process
    return getattr(exc, STARTED_PROCESS_ATTRIBUTE, None)


def reap_owned(pgid, directory=None, settle_seconds=None,
               sleeper=None, clock=None):
    """Reap a process group THIS component recorded as its own.

    Within this function, a group the ledger does not name is refused,
    and so are group 0, group 1, and this process's own group. Unlike `reap_group` it does
    NOT require the leader to still be alive, because the ledger — not
    the leader — is the ownership evidence, and an orphaned group with
    a dead leader is precisely the case that must remain reapable.

    Task 8 R27: the ledger gate's read (``owned_groups``) never waits. A
    ledger PRESENT but NOT OBSERVED is refused as
    ``REFUSED_LEDGER_UNAVAILABLE`` — never as "not in the ledger", and nothing
    is signalled — so a caller (``verification.run`` after its command's
    wait; the role turn's ``finally``) RETURNS, unsettled, instead of
    waiting, and the group stays reapable once the ledger reads.

    Task 8 R28-1: the ledger's membership is HISTORICAL — it names a number.
    The CURRENT group is signalled only when its ownership is proven, fresh, at
    the action boundary, by one of two proofs:

    - THIS process HOLDS the recorded leader — its own spawn for this ledger,
      not collected (``_leader_hold``): no other process can then hold the
      number. So a leader that EXITED with owned descendants still running is
      reaped (its uncollected zombie holds the number): positive cleanup.
    - otherwise the recorded leader is ALIVE and corroborated by its owned
      root beside the ledger — its nonce, and its start time now equal to the
      one recorded (``_recorded_leader_proves``, ``group_is_ours``): the proof
      recovery uses, for a spawn this process does not hold (a harness that
      exited, a Runtime that died).

    Otherwise nothing is signalled: ``ALREADY_GONE`` when no group by that
    number is alive; ``REFUSED_CURRENT_GROUP_UNPROVEN`` when one is (reused,
    leaderless, unbound — unresolved); ``REFUSED_CURRENT_GROUP_UNAVAILABLE`` when
    the proof could not be read. Neither refusal is a settlement. A held leader
    is collected by the reap itself (``_reap_leader``), AFTER the signal.

    The proof-versus-effect limit, stated. The proof and the signal are ONE span
    (see "THE SPAN"): for a leader this process registered it holds the hold's
    lock and the handle's collection lock (``Popen._waitpid_lock``) from the
    proof to the signal, so no collection of that leader through its handle —
    from ANY thread of this process — can fall between them; the acquisition is
    BOUNDED, and a lock held past the bound refuses
    (``REFUSED_CURRENT_GROUP_UNAVAILABLE``) rather than waiting. A collector that
    BYPASSES the handle (a raw ``waitpid`` on the pid, ``waitpid(-1)``, a kernel
    that collects children itself) is not excluded by any lock of this process:
    none exists in the product source as searched, and SIGCHLD ignored is
    refused. The CORROBORATED proof is a reading of a live process another
    parent may collect: the number could be released and reused between that
    reading and the signal only if, in that interval, the leader exits, is
    collected, every member of its group exits, and a new process is given the
    number and makes a group of it — the residual recovery already carries. No
    OS atomicity is claimed for either.
    """
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 1:
        return REFUSED_NOT_IN_LEDGER, (
            "%r is not a usable process group id" % (pgid,)
        )
    if pgid == os.getpgrp():
        return REFUSED_NOT_IN_LEDGER, (
            "refusing to reap this process's OWN group"
        )
    try:
        named = owned_groups(directory)
    except ObservationUnavailable as exc:
        # Task 8 R27: the gate's OWN observation failed — the ledger is present
        # but not observed. Nothing is signalled from a failed proof (the
        # zero-reap unavailable-evidence rule): refused, the group untouched,
        # reapable once the ledger reads.
        return REFUSED_LEDGER_UNAVAILABLE, (
            "group %d cannot be checked against this component's owner ledger"
            " (%s); nothing is signalled from a failed proof" % (pgid, exc))
    if pgid not in named:
        return REFUSED_NOT_IN_LEDGER, (
            "group %d is not recorded in this component's owner"
            " ledger; ownership is recorded evidence, never a name,"
            " a command pattern or a start time" % pgid
        )
    # Task 8 R28-1: the ledger names a NUMBER — historical membership. The
    # CURRENT group is proven this component's only while THIS process holds
    # its recorded leader, uncollected (``_leader_hold``, read fresh here, at
    # the action boundary): no other process can then hold the number or
    # create a group by it. Otherwise nothing is signalled — a group observed
    # gone is nothing to do; a live one is unresolved, never settled.
    # Task 8 R28-1, THE SPAN: the proof and the signal are ONE critical section
    # with respect to every collection of the leader through its handle — the
    # hold's lock, then the handle's own collection lock (``Popen._waitpid_lock``,
    # which CPython's every collection and this module's ``_collect_leader``
    # take), held from the proof to the signal and released before anything
    # collects. Acquired with a BOUND: a lock held past it (a wait in progress on
    # another thread) refuses, unresolved — the reap never waits on it.
    leader = _owned_leader(pgid)
    taken, signal_error = [], None
    try:
        for lock in _span_locks(leader):
            if not lock.acquire(timeout=_SPAN_LOCK_SECONDS):
                return REFUSED_CURRENT_GROUP_UNAVAILABLE, (
                    "group %d is named by this component's owner ledger, but an"
                    " observation or collection of its leader is in progress in this"
                    " process, so its proof cannot be held through the signal; nothing"
                    " is signalled — unresolved" % pgid)
            taken.append(lock)
        hold, why = _leader_hold(pgid, directory, leader)
        if hold == HOLD_UNAVAILABLE:
            return REFUSED_CURRENT_GROUP_UNAVAILABLE, (
                "group %d is named by this component's owner ledger, but %s; nothing is"
                " signalled from a proof not made — unresolved" % (pgid, why))
        if hold != HOLD_HELD:
            if not _group_alive(pgid):
                return ALREADY_GONE, None
            # Not held here (another process's spawn — a harness that exited, a
            # Runtime that died — or a leader already collected): the recorded
            # leader must be ALIVE and corroborated by its owned root, exactly as
            # recovery requires (``group_is_ours``).
            proven, why_not = _recorded_leader_proves(pgid, directory)
            if proven is None:
                return REFUSED_CURRENT_GROUP_UNAVAILABLE, (
                    "group %d is named by this component's owner ledger and a group by"
                    " that number is alive, but %s, and its recorded leader's proof"
                    " cannot be made (%s); nothing is signalled — unresolved"
                    % (pgid, why, why_not))
            if not proven:
                return REFUSED_CURRENT_GROUP_UNPROVEN, (
                    "group %d is named by this component's owner ledger and a group by"
                    " that number is alive, but %s, and %s: a group id is a NUMBER,"
                    " reused once its leader is collected, so the live group is not"
                    " proven the one recorded; nothing is signalled — unresolved"
                    % (pgid, why, why_not))
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError as exc:
            signal_error = exc
    finally:
        for lock in reversed(taken):
            lock.release()
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    settle = (
        REAP_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    )
    if signal_error is not None:
        if signal_error.errno not in (errno.ESRCH, errno.EPERM):
            return REFUSED_NOT_IN_LEDGER, (
                "could not signal group %d: %s" % (pgid, signal_error)
            )
        # Task 8 R28-1: the group held only the held leader's ZOMBIE (observed
        # on macOS: such a group answers EPERM) or no member at all. The leader
        # is collected, THEN the group observed: gone is nothing left to
        # signal; still alive is a member this process may not signal.
        _reap_leader(pgid)
        if not _group_alive(pgid):
            return ALREADY_GONE, None
        return REFUSED_NOT_IN_LEDGER, (
            "could not signal group %d: %s" % (pgid, signal_error)
        )
    deadline = clock() + settle
    while clock() < deadline:
        _reap_leader(pgid)
        if not _group_alive(pgid):
            return REAPED, None
        sleeper(REAP_POLL_SECONDS)
    _reap_leader(pgid)
    if _group_alive(pgid):
        return REFUSED_NOT_IN_LEDGER, (
            "group %d still has a live member %.1fs after SIGKILL"
            % (pgid, settle)
        )
    return REAPED, None


def surviving_owned_groups(directory=None):
    """Ledger-recorded groups that still have a live member.

    THE EXECUTED PIN for "no descendant survives": a harness calls
    this after its run and asserts the result is empty. Within its output only ledger-recorded groups appear, so accusing an
    unrelated process is outside its reportable range.

    Task 8 R27: a ledger PRESENT but NOT OBSERVED raises
    ``ObservationUnavailable`` (``owned_groups``) — never an empty list, which
    is the false "no survivor" this pin exists to prevent.
    """
    return sorted(
        pgid for pgid in owned_groups(directory)
        if _group_alive(pgid)
    )


# --------------------------------------------------------------------
# R-18 H-2: THE GATE — a harness must be ABLE to stop emitting
# --------------------------------------------------------------------
#
# G-3 required the source be stopped before the sink is proven. # In the event that produced R-18 the emitter was stopped BY THE
# OPERATOR, and this component had no gating of its own to exercise. # A future unattended run has no human to depend on noticing, which is
# the premise of the whole mission, so the capability lives here and is
# pinned.

GATE_FILE_NAME = "spawning-gated"

REFUSED_GATED = "refused_spawning_gated"


class SpawnGated(Exception):
    """Raised by `spawn_owned` when spawning has been gated."""


class SpawnUnconfirmed(Exception):
    """Task 8 R26: raised by ``spawn_owned`` AFTER ``Popen`` returned, when
    the PARENT could not confirm the owned root's stamp (the shared writer,
    ``spawn_stamp._write_record``, refused what it examined, could not
    publish its replacement — the record then left as it was — or published
    it without proving it: ``spawn_stamp.PublicationUnproven``, never undone).
    A child process STARTED: never "no spawn". Whether it ran the
    actual command is UNRESOLVED from here — the child stamps its own root
    before it execs and refuses to exec when it cannot, and the two stamps
    are not ordered. ``process`` is the started child, ``root`` its owned
    root, and the cause is chained. Deliberately NOT an ``OSError``, so no
    handler that reads an ``OSError`` as "refused before any process
    started" can classify it."""

    def __init__(self, process, root, cause):
        super(SpawnUnconfirmed, self).__init__(
            "a process STARTED (pid %s) and its owned root %s could not be stamped by"
            " the parent (%s: %s); whether it runs, and what it ran, is UNRESOLVED"
            % (process.pid, root, cause.__class__.__name__, cause))
        self.process = process
        self.root = root


def gate_path(directory=None):
    if not directory:
        return None
    return os.path.join(directory, GATE_FILE_NAME)


def gate_spawning(directory, reason):
    """Stop this component spawning anything further.

    Durable rather than in-memory, and the reason is that the process
    which must stop emitting may not be the process that decides to
    stop it: a supervisor, a signal handler, or a later run reads the
    same file. An in-memory flag would be invisible across that
    boundary.
    """
    path = gate_path(directory)
    if path is None:
        return False
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("%s\n%s\n" % (time.time(), reason))
        handle.flush()
        os.fsync(handle.fileno())
    return True


def spawning_is_gated(directory=None):
    path = gate_path(directory)
    return bool(path) and os.path.exists(path)


def ungate_spawning(directory):
    path = gate_path(directory)
    if path and os.path.exists(path):
        os.unlink(path)
        return True
    return False


# --------------------------------------------------------------------
# R-16 F-3: THE POST-HARNESS SWEEP
# --------------------------------------------------------------------

def sweep_owned(directory, settle_seconds=None, sleeper=None,
                clock=None):
    """Reap every ledger-recorded group that is still alive.

    THE POST-HARNESS GUARANTEE. A harness calls this from its own exit
    path; the assertion that it worked is `surviving_owned_groups`
    returning empty afterwards.

    Ownership is RE-DERIVED HERE, at the moment of reaping, from the
    ledger, rather than from a list transcribed earlier; the residual is
    that the ledger read is itself a snapshot, so a group that dies
    between the read and the signal is reported ALREADY_GONE rather than
    reaped. Within this window a group that was owned when someone last looked
    may be gone, and a group spawned since sits outside any transcribed
    set (R-17 G-1).

    Returns ``(reaped, still_alive, pending)``. ``pending`` carries
    nonces from spawns that crashed before their group could be
    recorded: those are REPORTED and left alone within this function, because the
    broader kill that would reach them is what ownership discipline
    forbids.
    """
    # Task 8 R27: the PENDING observation is made FIRST, before any group is
    # signalled: a ledger that cannot be observed raises
    # (``ObservationUnavailable``) with NO effect taken — never an empty
    # pending result, and never a failure surfacing only after reaps it would
    # misreport as none. A ledger that becomes unobservable later raises from
    # ``surviving_owned_groups`` before any signal, or makes ``reap_owned``
    # refuse (``REFUSED_LEDGER_UNAVAILABLE``) a group, returned not reaped:
    # the partial outcome as it stands.
    pending = pending_nonces(directory)
    reaped, stuck = [], []
    for pgid in surviving_owned_groups(directory):
        verdict, _detail = reap_owned(
            pgid, directory=directory, settle_seconds=settle_seconds,
            sleeper=sleeper, clock=clock,
        )
        if verdict in (REAPED, ALREADY_GONE):
            reaped.append(pgid)
        else:
            stuck.append(pgid)
    return reaped, stuck, pending


def install_exit_sweep(directory, settle_seconds=None):
    """Register `sweep_owned` to run when THIS process exits.

    `atexit` alone is not enough and is not claimed to be: it does not
    run on SIGKILL, and it did not run when the emitter in R-18 was
    terminated. It is registered because it covers the ordinary exit
    and the uncaught-exception path, which is where the earlier leak
    actually escaped; the residual — a hard kill — is what the GATE
    and a next run's opening sweep exist to cover instead.
    """
    import atexit
    atexit.register(sweep_owned, directory, settle_seconds)
    return True


# --------------------------------------------------------------------
# R-19 I-3: # RECOVERY OF ORPHANS ABSENT FROM THE LEDGER
# --------------------------------------------------------------------
#
# The ledger reaps what it recorded. # Orphans that PREDATE it sit outside its reach — a run that crashed
# before the ledger existed, a superseded build, or a Runtime restarting
# after a crash inherits groups its own ledger does not list. That is an ABSENT
# CAPABILITY rather than a bookkeeping bug, and fixing the finalizer
# alone leaves it open: the finalizer would pass its own tests and the
# mission would still fail, because unattended reliability is exactly
# the case where the previous run is the one that died.
#
# The evidence that survives a lost ledger is the OWNED ROOT: a
# directory this component created, one per spawn, under a DI-owned
# prefix, holding the group id it recorded and the scratch files the
# child uses. The directory is the record. It is ownership evidence in
# the same sense the ledger is — something this component made and can
# point at — and it is NOT a name or a command pattern, which remain
# outside what any recovery here will match on.

#: The child-side stamping wrapper (R-28 T-3), located next to
#: this module so a spawned child needs no import path.
_STAMP_WRAPPER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "spawn_stamp.py"
)

OWNED_ROOT_DIR_NAME = "di-owned-roots"
OWNED_ROOT_PGID_FILE = "pgid"
OWNED_ROOT_NONCE_FILE = "nonce"
OWNED_ROOT_START_FILE = "leader-start"

#: Why a recorded group is NOT treated as ours. Within recovery it is
#: reported and left alone (R-54 AR-3).
UNCORROBORATED_NO_START = (
    "no leader start time was recorded, so the group id cannot be"
    " distinguished from a reused one"
)
UNCORROBORATED_START_MISMATCH = (
    "the live group leader started at a different time than the one"
    " this record names; the group id has been REUSED"
)
UNCORROBORATED_NO_NONCE = (
    "the root carries no nonce, so it is not bound to a spawn this"
    " component made"
)
UNCORROBORATED_NONCE_MISMATCH = (
    "the recorded nonce does not name this root"
)
#: Task 8 R21-B: a corroboration record (the nonce or the leader start)
#: exists and cannot be read or does not decode — ownership UNAVAILABLE,
#: never "not ours" and never "no nonce"; within recovery reported with the
#: group and never signalled.
UNCORROBORATED_UNAVAILABLE = (
    "unavailable, not absent: a corroboration record of this root (its nonce"
    " or leader start time) cannot be read or does not decode, so whether the"
    " group is ours cannot be known"
)
#: Task 8 R22-1: the group is ALIVE and its recorded leader is GONE — its
#: descendants survive, so the leader's start time cannot be compared and
#: the group cannot be corroborated as ours. UNRESOLVED: reported with the
#: group, never signalled, never read as gone.
UNCORROBORATED_LEADERLESS = (
    "its recorded leader is gone while the group survives in its descendants,"
    " so whether the group is ours cannot be corroborated — unresolved, never"
    " signalled"
)
#: Task 8 R22-1: the group is ALIVE, its recorded leader was OBSERVED to
#: exist, and the query for the leader's start time FAILED — the
#: corroboration is UNAVAILABLE, not absent: reported, never signalled.
UNCORROBORATED_LEADER_UNAVAILABLE = (
    "unavailable, not absent: the recorded leader is alive and its start time"
    " could not be read (the query failed), so whether the group is ours"
    " cannot be known — unresolved, never signalled"
)
#: Task 8 R22-1: the group is ALIVE, and whether its recorded leader EXISTS
#: could not be determined (the OS answer could not be read) — nor its
#: start time. Existence UNPROVEN, never asserted either way: UNAVAILABLE,
#: reported, never signalled.
UNCORROBORATED_LEADER_UNDETERMINED = (
    "unavailable, not absent: whether the recorded leader exists could not be"
    " determined and its start time could not be read, so whether the group"
    " is ours cannot be known — unresolved, never signalled"
)
#: Every reason a LIVE group's leader corroboration was UNAVAILABLE for.
LEADER_UNAVAILABLE_REASONS = (UNCORROBORATED_LEADER_UNAVAILABLE,
                              UNCORROBORATED_LEADER_UNDETERMINED)
#: Task 8 R27-1: a LIVE group whose recorded start time is a strict PREFIX of
#: its live leader's — the start record of THIS leader, CUT SHORT (an earlier,
#: truncating stamp writer could leave one). It is never read as a reused id:
#: ``ps -o lstart=`` writes complete start times at one fixed width, so a
#: complete record is never a strict prefix of another — and were it ever, the
#: result is only retention. Unresolved; never signalled.
UNCORROBORATED_START_FRAGMENT = (
    "the recorded start time is a strict prefix of the live leader's: a record"
    " cut short, so the group may be ours — unresolved, never signalled, never"
    " read as reused"
)
#: Task 8 R27-1: the scope's OWNER LEDGER names a DIFFERENT group for this
#: root's spawn nonce than the root's own group record — two independently
#: written records CONTRADICT each other, so neither establishes absence.
#: Reported; never signalled — neither group is acted on.
UNCORROBORATED_LEDGER_CONTRADICTS = (
    "the owner ledger names a different group for this root's spawn than the"
    " root's own group record — contradictory records: unresolved, never"
    " signalled, never read as gone"
)


def default_base():
    """The root the MACHINE-GLOBAL scope stores hang under.

    ONE function, and both stores resolve through it — which is the
    whole reason it exists. R-47 found a test harness deleting from
    the shared bases because it could REACH them; a single seam is
    what lets a test process redirect BOTH stores at once, so within
    such a process the shared base is not somewhere it can write and
    then feel obliged to tidy. Construction rather than convention:
    within such a process the fix for a destructive cleanup is code
    with no shared store to clean.

    Production has one implementation and does not override it.
    """
    import tempfile
    return tempfile.gettempdir()


def owned_root_base(base=None):
    """The durable prefix under which per-spawn owned roots live."""
    if base:
        return os.path.join(base, OWNED_ROOT_DIR_NAME)
    return os.path.join(default_base(), OWNED_ROOT_DIR_NAME)


def create_owned_root(nonce, base=None):
    """Create this spawn's owned root BEFORE the process exists.

    Returns the directory. It is created first, so within the rest of this call a crash still
    leaves a durable artifact naming the spawn — the property the
    deleted-temp-directory version lacked, and the reason four orphans
    in this increment became unattributable. Outside that: a crash BEFORE this line leaves no artifact, and this
    function claims none for it.
    """
    root = os.path.join(owned_root_base(base), nonce)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, OWNED_ROOT_NONCE_FILE), "w",
              encoding="utf-8") as handle:
        handle.write(nonce)
        handle.flush()
        os.fsync(handle.fileno())
    return root


def record_owned_root_group(root, pgid):
    """Stamp the group id AND its leader's start time into a root.

    Routed through `spawn_stamp.stamp` rather than writing the pgid
    here, so the parent-side stamp and the child-side stamp produce
    the SAME record. Two writers of one record is how a corroboration
    file comes to be written by one path and missing from the other,
    and within recovery that is indistinguishable from a reused id.

    Task 8 R26: that writer validates what it opened before writing, and its
    open of a FIFO does not wait for a reader, so a record path that is a
    FIFO, a link (where the platform provides ``O_NOFOLLOW``) or anything but
    a single-named regular file RAISES an ``OSError`` here, with nothing
    written. No regular-file write or ``fsync`` latency is bounded.

    Task 8 R27-1: the writer REPLACES each record atomically, so this
    confirmation, failing or interrupted after the child stamped itself,
    leaves the child's own records complete (or a record still absent) —
    never a truncated, empty or partial record that reads as a different,
    gone group. A failure after a record's publication raises
    ``spawn_stamp.PublicationUnproven``: the record stays published, never
    undone. What each failure leaves is stated at ``spawn_stamp._write_record``.
    """
    from target_runtime import spawn_stamp as _stamp
    return _stamp.stamp(root, pgid)


def leader_start_time(pid):
    """The live start time of ``pid``. ONE definition, in
    `spawn_stamp`, because the stamp writes it and recovery compares
    it: two implementations of "when did this start" is two ways to
    disagree about whether a group is ours."""
    from target_runtime import spawn_stamp as _stamp
    return _stamp.leader_start_time(pid)


#: Task 8 R26-1: the bound on ONE ownership record read by its consumers —
#: the binding key, an owned root's nonce, leader-start or group record.
#: Every record this module writes is far smaller.
OWNERSHIP_RECORD_BYTES = READ_BLOCK_BYTES
#: ... and on ONE assignment credential. It is larger, so that a credential
#: the writer never made is still READ and judged by its content (malformed,
#: forged) as before — R23-2's decoder and encoder containment rests on that —
#: while the read stays bounded.
CREDENTIAL_RECORD_BYTES = 1024 * 1024


class NotARegularRecord(OSError):
    """Task 8 R26-1: an ownership record that EXISTS but is not a regular
    file or a directory — a FIFO, socket or device, pre-existing or
    substituted after an earlier observation, reached by name or through a
    link. It is an ``OSError``, so every consumer's existing ``except OSError``
    reports it as unavailable (never absent, never malformed)."""


class OversizedRecord(OSError):
    """Task 8 R26-1: an ownership record larger than
    ``OWNERSHIP_RECORD_BYTES``, or one that grew past it while it was read.
    It is never read whole; an ``OSError``, reported as unavailable."""


def read_ownership_record(path, limit=OWNERSHIP_RECORD_BYTES):
    """Task 8 R26-1: the bytes of ONE ownership record. Every consumer of a
    credential, the binding key, or an owned root's nonce, leader-start or
    group record reads it HERE, and nowhere else:

    - ONE open, ``O_RDONLY | O_NONBLOCK``. An open that would wait (a FIFO
      with no writer) returns at once instead. A link is FOLLOWED, exactly
      as the plain ``open`` this replaces did, so a VALID linked record reads
      as before.
    - ``fstat`` on the OPENED descriptor, before any byte is read. A
      directory raises ``IsADirectoryError``, as ``open`` did, so each
      consumer's existing classification of it stands. Anything else that is
      not a regular file raises ``NotARegularRecord``. So a type substituted
      after any earlier observation is judged by what was actually opened.
    - BOUNDED by ``limit`` (``OWNERSHIP_RECORD_BYTES``; a credential's reader
      passes ``CREDENTIAL_RECORD_BYTES``). A larger record raises
      ``OversizedRecord`` unread. The read is in ``READ_BLOCK_BYTES`` blocks,
      never more than ``limit`` + 1 bytes, and a record that grew past
      ``limit`` while read raises it too.

    ``FileNotFoundError`` and every other ``OSError`` from the open propagate
    exactly as before. So each consumer's genuine-absence classification
    (``classify_missing``) is unchanged."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)
        if not stat.S_ISREG(info.st_mode):
            raise NotARegularRecord(errno.EINVAL, "not a regular file", path)
        if info.st_size > limit:
            raise OversizedRecord(errno.EFBIG, "larger than %d bytes" % limit, path)
        blocks, count = [], 0
        while count <= limit:
            block = os.read(descriptor, min(READ_BLOCK_BYTES, limit + 1 - count))
            if not block:
                break
            blocks.append(block)
            count += len(block)
        if count > limit:
            raise OversizedRecord(errno.EFBIG, "grew past %d bytes while read" % limit, path)
        return b"".join(blocks)
    finally:
        os.close(descriptor)


def owned_root_record(directory):
    """Everything an owned root durably claims: nonce, pgid, start.

    A record, not a verdict. `group_is_ours` is what turns it into
    one.

    Task 8 R21-B: read STRICTLY. A record file that is ABSENT
    (``FileNotFoundError``) is a field never written: None. One that cannot
    be read RAISES its ``OSError``, and one that does not decode RAISES
    ``UnicodeDecodeError`` — never read as absent, which within
    `group_is_ours` would have reported an unreadable nonce as "no nonce".
    Every caller of `group_is_ours` classifies both as unavailable.
    """
    record = {"nonce": None, "pgid": None, "leader_start": None}
    for key, name in (
        ("nonce", OWNED_ROOT_NONCE_FILE),
        ("leader_start", OWNED_ROOT_START_FILE),
        ("pgid", OWNED_ROOT_PGID_FILE),
    ):
        path = os.path.join(directory, name)
        try:
            # Task 8 R26-1: non-waiting, descriptor-validated, bounded.
            value = read_ownership_record(path).decode("utf-8").strip()
        except FileNotFoundError:
            # Task 8 R24-2: a field never written only when the traversal
            # says the record is GENUINELY missing; a dangling record link is
            # a record that cannot be read, and RAISES like any other.
            if classify_missing(path).availability == READ_ABSENT:
                continue
            raise
        if key == "pgid":
            try:
                value = int(value)
            except ValueError:
                value = None
        record[key] = value or None
    return record


def group_is_ours(directory, record=None):
    """``(pgid_or_None, reason_or_None)`` — the AR-3 corroboration.

    THE RULE THIS ENFORCES: a recorded pgid is not a durable identity.
    Process-group numbers are reused, so "group N is recorded here and
    group N is alive" is two facts about a NUMBER and no fact about a
    PROCESS. The specimen: pgid 44603 was recorded in a root this
    component wrote, was later held by a system crash reporter, and
    was empty by the time it was re-checked. Signalling it on the
    strength of the record alone would have killed an unrelated
    process — the one act the whole ownership discipline forbids.

    So a group is ours only when the record CORROBORATES it:

      * the root carries the NONCE that names it, binding the record
        to a spawn this component made; and
      * the leader's start time NOW equals the start time recorded
        when the group was stamped.

    Anything else — no nonce, no recorded start, a start that differs —
    returns a reason and no pgid; a group observed GONE returns no reason
    (nothing to do). Fail-closed in the direction that matters: within
    recovery an uncorroborated group is reported and left alone rather
    than signalled. Task 8 R22-1: a LIVE group whose leader's start time
    cannot be obtained is never "gone" — UNCORROBORATED_LEADERLESS when
    its leader is observed gone and descendants survive,
    UNCORROBORATED_LEADER_UNAVAILABLE when the leader is observed alive
    (the query failed), UNCORROBORATED_LEADER_UNDETERMINED when whether the
    leader exists could not be read either.

    THIS IS NOT THE RESEMBLANCE MATCHING THE MODULE FORBIDS, and the
    difference is worth stating because both touch `ps`. Resemblance
    matching ENUMERATES what is running and picks processes that look
    like ours. This asks the OS ONE question about ONE pid THIS
    COMPONENT RECORDED, and uses the answer only to decide whether the
    record still refers to what it referred to when it was written.
    Within this gate it can only narrow what is acted on; it adds no
    process to the set.
    """
    record = owned_root_record(directory) if record is None else record
    pgid = record.get("pgid")
    if pgid is None:
        return None, None                       # unstamped, not ours
    nonce = record.get("nonce")
    if not nonce:
        return None, UNCORROBORATED_NO_NONCE
    if nonce != os.path.basename(directory.rstrip(os.sep)):
        return None, UNCORROBORATED_NONCE_MISMATCH
    recorded_start = record.get("leader_start")
    if not recorded_start:
        return None, UNCORROBORATED_NO_START
    live_start = leader_start_time(pgid)
    if live_start is None:
        # Task 8 R22-1: a None from the leader query is NOT evidence — it is
        # both "the leader is gone" and "the query failed". Only a GROUP
        # observed gone is nothing to do; a live group is told apart by
        # asking the OS whether its recorded LEADER exists (signal 0: no
        # signal is delivered) — and is UNRESOLVED either way, never gone.
        if not _group_alive(pgid):
            return None, None                   # the group is gone: nothing to do
        leader = _process_exists(pgid)
        if leader is False:
            return None, UNCORROBORATED_LEADERLESS
        if leader is True:
            return None, UNCORROBORATED_LEADER_UNAVAILABLE
        return None, UNCORROBORATED_LEADER_UNDETERMINED   # existence unproven
    if live_start != recorded_start:
        if live_start.startswith(recorded_start):
            # Task 8 R27-1: THIS leader's start, cut short — never a reused id.
            return None, UNCORROBORATED_START_FRAGMENT
        return None, UNCORROBORATED_START_MISMATCH
    return pgid, None


#: Task 8 R27-1: the bound on ONE owner-ledger read (``ledger_groups``).
OWNER_LEDGER_BYTES = CREDENTIAL_RECORD_BYTES


def ledger_groups(scope):
    """Task 8 R27-1: the scope's OWNER LEDGER (``record_owned_group``), read
    ONLY so its readers can see an actual CONTRADICTION (``ledger_contradicts``)
    — as ``(groups, gap)``, in THREE branches that are never collapsed:

    - GENUINELY ABSENT — no ledger directory, a ledger the traversal finds
      GENUINELY missing (``_observed_missing``), or (in ``groups``) no row
      for a nonce: ``({...}, None)`` — evidence of NOTHING; every caller
      decides exactly as without a ledger. The ledger is optional evidence:
      ``spawn_owned`` appends a spawn's group row only AFTER ``Popen``
      returns, so a validly child-stamped root can have no row (its parent
      died first), and a legacy root may predate the ledger — the
      parent-death and child-only recovery properties, preserved.
    - PRESENT BUT NOT OBSERVED — not a regular file, larger than
      ``OWNER_LEDGER_BYTES``, unreadable, a link that does not resolve, not
      UTF-8: ``(None, (path, reason))``, ``OBSERVATION_UNAVAILABLE`` — never
      absence (R21-2). Each caller routes it into ITS OWN established
      unavailable result: retained, never settled, never cleared.
    - READABLE: ``({nonce: {pgid, ...}}, None)``. A line that is not JSON
      names nothing, exactly as the ledger's own readers (``owned_groups``,
      ``pending_nonces``) read it — a torn last append.

    Read through ``read_ownership_record`` (Task 8 R26-1: non-waiting,
    descriptor-validated, bounded). ``scope`` is the directory the caller
    already holds — the one both production spawns pass as their ledger
    directory — so nothing ambient is consulted, and no group is ever acted on
    from it. Never raises."""
    path = ledger_path(scope)
    if path is None:
        return {}, None
    try:
        raw = read_ownership_record(path, OWNER_LEDGER_BYTES)
    except FileNotFoundError:
        gap = _observed_missing(path, "the owner ledger")
        return ({}, None) if gap is None else (None, gap)
    except OSError as exc:
        return None, _unavailable(path, "the owner ledger", exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, _unavailable(path, "the owner ledger", exc)
    found = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(row, dict):
            continue
        nonce, pgid = row.get("nonce"), row.get("pgid")
        if isinstance(nonce, str) and isinstance(pgid, int) and not isinstance(pgid, bool):
            found.setdefault(nonce, set()).add(pgid)
    return found, None


def ledger_contradicts(groups, root, pgid):
    """Task 8 R27-1: True ONLY for an actual CONTRADICTORY ROW — the readable
    ledger's ``groups`` (``ledger_groups``) name at least one group for
    ``root``'s spawn nonce (its directory's name) and ``pgid``, the root's own
    record, is none of them. No row for the nonce, or ``pgid`` among them:
    False — a missing row is evidence of nothing. For a ledger that was NOT
    OBSERVED (``groups`` None) it ALSO returns False, and that False means
    only that no contradiction could be READ — never that none exists. Each
    caller has already routed the gap into its own unavailable result, and
    that result governs."""
    if not groups:
        return False
    named = groups.get(os.path.basename(root.rstrip(os.sep)))
    return bool(named) and pgid not in named


#: Task 8 R21-2: how an observation that could not be made is REPORTED —
#: unavailable, never absent.
OBSERVATION_UNAVAILABLE = "unavailable, not absent"


class ObservationUnavailable(Exception):
    """Task 8 R21-2: an ownership observation that could not be made — an
    unreadable scope base or entry, owned-root prefix, owned root or group
    record. Raised where a caller would otherwise receive FEWER results
    (indistinguishable from an empty store); ``unavailable`` carries every
    such ``(path, reason)``. Nothing it covers is acted on."""

    def __init__(self, unavailable):
        self.unavailable = list(unavailable)
        super(ObservationUnavailable, self).__init__(
            "; ".join("%s: %s" % (path, reason) for path, reason in self.unavailable))


def _unavailable(path, what, exc):
    return path, "%s: %s cannot be read (%s)" % (
        OBSERVATION_UNAVAILABLE, what, exc.__class__.__name__)


def _observed_missing(path, what):
    """Task 8 R24-2: after ``stat`` or ``open`` of ``path`` raised
    ``FileNotFoundError``. ``None`` when ``path`` is GENUINELY missing (the
    traversal, ``classify_missing``: a component is missing and every one
    before it was reachable) — today's absence, unchanged. Otherwise the
    ``(path, reason)`` observation that could not be made: ``stat`` raises
    that same error for an EXISTING link whose target is unavailable, at the
    path or at an ancestor, and that is never absence."""
    missing = classify_missing(path)
    if missing.availability == READ_ABSENT:
        return None
    return path, "%s: %s cannot be read (%s)" % (
        OBSERVATION_UNAVAILABLE, what, missing.problem)


def owned_roots(base=None):
    """``(directory, pgid_or_None)`` for every owned root on disk.

    A root whose pgid file is absent is reported with None rather than
    skipped: it is a spawn that crashed before its group could be
    stamped, and losing it silently is the failure this whole
    mechanism exists to prevent.

    Task 8 R21-2: and so is losing a root, a prefix or a group record that
    cannot be READ — they RAISE ``ObservationUnavailable`` (see
    ``owned_roots_observed``), never return as fewer roots, as no roots or
    as an unstamped root.
    """
    found, unavailable = owned_roots_observed(base)
    if unavailable:
        raise ObservationUnavailable(unavailable)
    return found


def owned_roots_observed(base=None):
    """Task 8 R21-2: ``(roots, unavailable)`` — ``owned_roots``' result read
    STRICTLY (``FileNotFoundError`` alone is absence; ``stat`` follows a
    link exactly as the ``isdir`` guard it replaces did), with every
    observation that could not be made as ``(path, reason)``: an owned-root
    prefix, a root or a group record that cannot be read, or a group record
    that does not decode — not UTF-8, not a number, or beyond any possible
    group id (R21-B). An EMPTY group record is a root not yet stamped
    (None), as before. The readable roots are still returned.

    Task 8 R24-2: ``FileNotFoundError`` is absence only when the traversal
    says so (``_observed_missing``); a dangling link at the prefix, at a root
    or at a group record — or among their ancestors — is an observation not
    made, never a prefix with no roots, a root gone meanwhile or a root not
    yet stamped."""
    prefix = owned_root_base(base)
    try:
        mode = os.stat(prefix).st_mode
    except FileNotFoundError:
        gap = _observed_missing(prefix, "the owned-root prefix")
        return [], ([] if gap is None else [gap])
    except OSError as exc:
        return [], [_unavailable(prefix, "the owned-root prefix", exc)]
    if not stat.S_ISDIR(mode):
        return [], [(prefix, "%s: the owned-root prefix is not a directory"
                     % OBSERVATION_UNAVAILABLE)]
    try:
        names = sorted(os.listdir(prefix))
    except OSError as exc:
        return [], [_unavailable(prefix, "the owned-root prefix", exc)]
    found, unavailable = [], []
    for name in names:
        directory = os.path.join(prefix, name)
        try:
            mode = os.stat(directory).st_mode
        except FileNotFoundError:
            gap = _observed_missing(directory, "the owned root")
            if gap is not None:
                unavailable.append(gap)
            continue                   # gone meanwhile (genuinely), or reported
        except OSError as exc:
            unavailable.append(_unavailable(directory, "the owned root", exc))
            continue
        if not stat.S_ISDIR(mode):
            continue                   # not a root (the prefix's freeze file, for one)
        path = os.path.join(directory, OWNED_ROOT_PGID_FILE)
        try:
            raw = read_ownership_record(path)       # Task 8 R26-1
        except FileNotFoundError:
            gap = _observed_missing(path, "the group record")
            if gap is None:
                found.append((directory, None))    # genuinely not yet stamped
            else:
                unavailable.append(gap)
            continue
        except OSError as exc:
            unavailable.append(_unavailable(path, "the group record", exc))
            continue
        if not raw:
            # The stamp opens its record and THEN writes it: an EMPTY record is
            # that window, or a spawn that crashed inside it — a root not (yet)
            # stamped, as ``owned_roots`` has always read it. (The retirement
            # refuses the same record as unreadable; both leave it alone.)
            found.append((directory, None))
            continue
        # Task 8 R21-B: read as BYTES and decoded HERE, at the read: ASCII
        # digits only, no longer than MAX_RECORDED_GROUP_ID and no larger. A
        # record that is not UTF-8 (byte-level), is not a number (textual) or
        # is beyond any possible group id "does not decode" — never an
        # exception escaping recovery: a text read raised UnicodeDecodeError,
        # which is not an OSError, past both read handlers, and an
        # out-of-range id overflowed ``os.killpg``. The small ids (0, 1) still
        # decode, and ``recover_orphans``' own guard skips them as before.
        text = raw.strip()
        if (not text or len(text) > len(str(MAX_RECORDED_GROUP_ID))
                or not all(48 <= byte <= 57 for byte in text)
                or int(text) > MAX_RECORDED_GROUP_ID):
            unavailable.append((path, "%s: the group record does not decode"
                                " (not a group id)" % OBSERVATION_UNAVAILABLE))
            continue
        found.append((directory, int(text)))
    return found, unavailable


def recover_orphans(base=None, settle_seconds=None, sleeper=None,
                    clock=None, unavailable=None):
    """Reap live groups recorded in OWNED ROOTS, ledger or no ledger.

    THE ABSENT CAPABILITY R-19 named. This is what a Runtime restarting
    after a crash runs: it inherits no ledger, and the owned roots on
    disk are what let it clean up after the run that died.

    Ownership is re-derived HERE, at the moment of recovery, from the
    roots present right now. Returns
    ``(recovered, stuck, unstamped, uncorroborated)``. ``unstamped``
    names roots for which no pgid was ever written; ``uncorroborated``
    names ``(directory, pgid, reason)`` for a LIVE group whose
    ownership, within this record, is unproven — a reused group id, a
    missing start time, an unnonced root. Both are reported and left alone here, because
    guessing at them is where a recovery turns into a name-pattern
    sweep, and signalling a reused id is how it turns into killing
    somebody else's process.

    Task 8 R21-2: an observation that cannot be made (``owned_roots_observed``)
    is REPORTED — into ``unavailable`` when the caller passes a list, else
    as ``ObservationUnavailable`` — and never read as "no roots"; nothing it
    covers is signalled. The readable roots are still recovered. R21-B: a
    LIVE group whose corroboration record cannot be read or does not decode
    is reported in ``uncorroborated`` with ``UNCORROBORATED_UNAVAILABLE`` (and
    into ``unavailable`` when a list is passed) and never signalled.

    Task 8 R27-1: a root whose group record the scope's owner ledger
    CONTRADICTS (``ledger_contradicts``) is reported in ``uncorroborated``
    with ``UNCORROBORATED_LEDGER_CONTRADICTS``, live or not, and NEITHER group
    is signalled; a live leader whose start record is cut short is reported
    with ``UNCORROBORATED_START_FRAGMENT``. A ledger genuinely absent, or no
    row, says nothing. A ledger PRESENT but NOT OBSERVED (``ledger_groups``'
    gap) is an observation not made, reported exactly as an unreadable root
    is — into ``unavailable``, or ``ObservationUnavailable`` without a list —
    once any root is stamped; and while it stays unobserved NOTHING in that
    scope is signalled or reaped, a corroborated live group included: the
    contradiction check is part of the ownership proof, and a readable
    contradictory row would have withheld the reap, so an UNOBSERVED one must
    not permit it (never fail-open). Only its unstamped roots are reported. A
    truthful hold: once the ledger reads, recovery proceeds as before.
    """
    recovered, stuck, unstamped, uncorroborated = [], [], [], []
    roots, missing = owned_roots_observed(base)
    if missing:
        if unavailable is None:
            raise ObservationUnavailable(missing)
        unavailable.extend(missing)
    # Task 8 R27-1: the owner ledger. Genuinely absent says nothing; PRESENT but
    # not observed is an observation not made — REPORTED exactly as a missing
    # root observation is (raised without a list), never read as absent — and
    # NOTHING in this scope is acted on while it stays unobserved: the
    # contradiction check is part of the ownership proof, so losing it loses
    # part of the proof. No group is signalled or reaped; a truthful hold.
    groups, ledger_gap = ledger_groups(base)
    if ledger_gap is not None and any(pgid is not None for _root, pgid in roots):
        if unavailable is None:
            raise ObservationUnavailable([ledger_gap])
        unavailable.append(ledger_gap)
        return recovered, stuck, [root for root, pgid in roots if pgid is None], uncorroborated
    for directory, pgid in roots:
        if pgid is None:
            unstamped.append(directory)
            continue
        if ledger_contradicts(groups, directory, pgid):
            # Task 8 R27-1: the owner ledger names a DIFFERENT group for this
            # spawn — contradictory records, live or not: REPORTED, and neither
            # the recorded group nor the ledger's is signalled.
            uncorroborated.append((directory, pgid, UNCORROBORATED_LEDGER_CONTRADICTS))
            continue
        if pgid <= 1 or pgid == os.getpgrp():
            continue
        if not _group_alive(pgid):
            continue
        # R-54 AR-3: ALIVE IS NOT OURS. Within this check a live
        # number says nothing about whether the process holding it is
        # the one this record names, because the OS reuses the
        # number. Corroborate before signalling.
        try:
            ours, reason = group_is_ours(directory)
        except (OSError, UnicodeDecodeError) as exc:
            # Task 8 R21-B: a corroboration record that cannot be read or
            # does not decode (``owned_root_record`` raises; nothing else in
            # ``group_is_ours`` can) — REPORTED with the live group as
            # unavailable (and, given the caller's list, counted among the
            # observations not made), never signalled, never escaping
            # recovery: it is met mid-walk, after earlier roots were acted
            # on, so it is never raised.
            reason = "%s (%s)" % (UNCORROBORATED_UNAVAILABLE, exc.__class__.__name__)
            uncorroborated.append((directory, pgid, reason))
            if unavailable is not None:
                unavailable.append((directory, reason))
            continue
        if ours is None:
            if reason is not None:
                uncorroborated.append((directory, pgid, reason))
            # Task 8 R22-1: a leader query that FAILED is an observation not
            # made — counted with the unavailable ones (a leaderless group is
            # an observation made: reported above, never signalled).
            if reason in LEADER_UNAVAILABLE_REASONS and unavailable is not None:
                unavailable.append((directory, reason))
            continue
        verdict, _detail = reap_group_by_recorded_root(
            ours, settle_seconds=settle_seconds, sleeper=sleeper,
            clock=clock,
        )
        (recovered if verdict in (REAPED, ALREADY_GONE)
         else stuck).append(ours)
    return recovered, stuck, unstamped, uncorroborated


def reap_group_by_recorded_root(pgid, settle_seconds=None,
                                sleeper=None, clock=None):
    """Reap a group whose id came from an OWNED ROOT on disk.

    Separate from `reap_owned` because the ownership EVIDENCE differs:
    `reap_owned` consults the ledger, this consults a directory this
    component created. Both are recorded evidence; neither is a name.
    The group's leader may be long dead, so leader verification is not
    required here — the root is the proof.

    Task 8 R28-1: when the leader is THIS process's own registered spawn, the
    signal runs in the same SPAN as ``reap_owned``'s (``_span_locks``: the hold's
    lock, then the handle's collection lock, BOUNDED), and under it the leader
    must still be uncollected and still the registration for the number — the
    number recovery's proof was made on is then still this process's. A leader
    collected since that proof refuses (``REFUSED_CURRENT_GROUP_UNPROVEN``): the
    number may already name another group. Another process's leader carries the
    residual stated at ``reap_owned`` (its parent may collect it between the
    proof and the signal).
    """
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    settle = (
        REAP_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    )
    leader = _owned_leader(pgid)
    taken, signal_error = [], None
    try:
        for lock in _span_locks(leader):
            if not lock.acquire(timeout=_SPAN_LOCK_SECONDS):
                return REFUSED_CURRENT_GROUP_UNAVAILABLE, (
                    "group %d: an observation or collection of its leader is in"
                    " progress in this process, so recovery's proof cannot be held"
                    " through the signal; nothing is signalled — unresolved" % pgid)
            taken.append(lock)
        if leader is not None and (_is_collected(leader)
                                   or _owned_leader(pgid) is not leader):
            return REFUSED_CURRENT_GROUP_UNPROVEN, (
                "group %d: its leader, this process's own spawn, was collected after"
                " recovery's proof, so the number may already name another group;"
                " nothing is signalled — unresolved" % pgid)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError as exc:
            signal_error = exc
    finally:
        for lock in reversed(taken):
            lock.release()
    if signal_error is not None:
        exc = signal_error
        if exc.errno == errno.ESRCH:
            _reap_leader(pgid)
            return ALREADY_GONE, None
        if exc.errno == errno.EPERM:
            # Task 8 R28-1: a group that holds only a leader's ZOMBIE answers
            # EPERM (observed on macOS) — a held leader this process kept
            # uncollected after a refused reap. It is collected, THEN the group
            # observed: gone is nothing left to signal.
            _reap_leader(pgid)
            if not _group_alive(pgid):
                return ALREADY_GONE, None
        return REFUSED_NOT_IN_LEDGER, (
            "could not signal group %d: %s" % (pgid, exc)
        )
    deadline = clock() + settle
    while clock() < deadline:
        _reap_leader(pgid)
        if not _group_alive(pgid):
            return REAPED, None
        sleeper(REAP_POLL_SECONDS)
    return (REFUSED_NOT_IN_LEDGER,
            "group %d still alive %.1fs after SIGKILL" % (pgid, settle))


# --------------------------------------------------------------------
# R-20 J-1: A FREEZE MUST NOT DEPEND ON BEING RECEIVED
# --------------------------------------------------------------------
#
# Twice in this increment a stop instruction sat QUEUED BEHIND THE VERY
# ACTIVITY IT EXISTED TO STOP, and both times an operator had to
# quiesce the emitter out of band before the message could land. A
# control path that reads as delivered, reports as consumed, and has
# no effect until someone intervenes is not a control path.
#
# So the freeze is DURABLE STATE THAT A SPAWN READS BEFORE EMITTING,
# not a message a busy process must first be idle enough to receive.
# `is_frozen` touches one file; # an emitter in a tight spawn loop checks it on every spawn, and a
# freeze written by another process — a
# supervisor, a signal handler, a later run — takes effect on the very
# next spawn without the emitter having to notice anything.
#
# The residual, stated with it: # a process already inside `Popen` when the freeze lands still completes
# that one spawn, and code that does not call `spawn_owned` is outside
# the freeze's reach. Recovery, not the
# freeze, is what covers those.

FREEZE_FILE_NAME = "SPAWNING-FROZEN"


def freeze_path(base=None):
    return os.path.join(owned_root_base(base), FREEZE_FILE_NAME)


def freeze_spawning(reason, base=None):
    """Freeze ALL owned spawning, durably and globally."""
    path = freeze_path(base)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("%s\n%s\n" % (time.time(), reason))
        handle.flush()
        os.fsync(handle.fileno())
    return path


def is_frozen(base=None):
    return os.path.exists(freeze_path(base))


UNFREEZE_LOG_NAME = "unfreeze-log.jsonl"


def unfreeze_log_path(base=None):
    return os.path.join(owned_root_base(base), UNFREEZE_LOG_NAME)


def thaw_spawning(base=None, reason=None, authority=None):
    """Lift the freeze, DELIBERATELY and ON THE RECORD (R-26 Q-1).

    A ruling PERMITS a lift; it does not perform one. Authority is not
    state — something must ACT, and the action must be RECORDED — so
    this writes an audit line naming the AUTHORITY it acts under and
    the REASON, and it writes that line BEFORE removing the freeze,
    on the same before-the-action discipline as K-1. A lift that left
    no record would be indistinguishable from someone deleting the
    file.

    ``reason`` and ``authority`` are required in practice: a lift
    recorded as `None`/`None` is legible as an undocumented one rather
    than being silently equivalent to a documented lift.
    """
    import json
    path = freeze_path(base)
    log = unfreeze_log_path(base)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "action": "unfreeze",
            "authority": authority,
            "reason": reason,
            "was_frozen": os.path.exists(path),
            "frozen_reason": freeze_reason(base),
            "by_pid": os.getpid(),
            "at": time.time(),
        }) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if os.path.exists(path):
        os.unlink(path)
        return True
    return False


def unfreeze_history(base=None):
    """Every recorded lift, so a reader can ask WHO lifted it and
    UNDER WHAT AUTHORITY rather than only whether it is lifted now."""
    import json
    log = unfreeze_log_path(base)
    if not os.path.exists(log):
        return []
    rows = []
    with open(log, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def spawning_refused_because(directory=None, base=None):
    """Which stop, if either, is in force — reported together.

    TWO mechanisms exist and R-26 Q-1 asked whether that is intentional
    or duplication. It is a REAL division that grew by accident and is
    now stated: the FREEZE is global and stops every owned spawn
    anywhere (a supervisor-level stop); the GATE is per-directory and
    lets one harness quiesce its OWN measurement without stopping
    anything else. The hazard is that a caller checks one and misses
    the other, so this reports both and `spawn_owned` consults both.
    """
    if is_frozen(base):
        return "frozen", freeze_reason(base)
    if directory and spawning_is_gated(directory):
        return "gated", None
    return None, None


def freeze_reason(base=None):
    """The recorded reason, so a refusal can say WHY rather than only
    that it refused."""
    path = freeze_path(base)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    return lines[1] if len(lines) > 1 else None


# --------------------------------------------------------------------
# R-43 AG-1..AG-5: ATTRIBUTION BY ASSIGNMENT, NOT BY NAME
# --------------------------------------------------------------------
#
# R-34 Z-1 asked for per-workflow attribution and the first fix
# achieved it BY NAME: a scope was a directory whose BASENAME carried
# the owning workflow id and task id, parsed back at recovery time.
# R-43 named the hole that leaves. A NAME IS A LABEL, NOT A
# CREDENTIAL: anything able to create a directory under the base can
# mint one whose name parses, and every fail-closed check downstream
# then validates the PARSE while the parse validates nothing within
# that check.
#
# So the credential is an ASSIGNMENT RECORD, written into a SEPARATE
# STORE BEFORE the spawn, carrying the exact owner type, owner id,
# unit id and control identity, bound by an HMAC over those fields
# under a key this component creates mode 0600. Enumeration reads the
# ASSIGNMENT. A directory whose name parses but which carries no valid
# assignment is UNATTRIBUTED: reported, left alone, never acted on
# within recovery.
#
# The residual, stated with it: whoever can READ the key file can
# forge an assignment. The binding raises forgery from "create a
# directory anyone can create" to "read a 0600 file"; it claims no
# more than that, and it is not a defence against this uid itself.

SCOPE_PREFIX = "scope"
SCOPE_SEPARATOR = "__"
SCOPE_OWNER_KEY = "owner="
SCOPE_CONTROL_KEY = "control="
SCOPE_ID_KEY = "id="
SCOPE_UNIT_KEY = "unit="

#: AG-5: a planning scope is its OWN owner type. It is not a workflow
#: wearing a parseable "planning-" prefix on the workflow id — that
#: shape made the two owners share one namespace, and a reader had to
#: infer which it was looking at from a substring.
OWNER_TYPE_WORKFLOW = "workflow"
OWNER_TYPE_PLANNING = "planning"
OWNER_TYPES = (OWNER_TYPE_WORKFLOW, OWNER_TYPE_PLANNING)

#: The unit id a pre-record planning scope carries: there is no task
#: yet, and the scope still names exactly one owner.
PLANNING_UNIT_ID = "pre-record"

#: A planning turn's owner id. The CONTROL REPOSITORY is the owner and
#: it is already carried as its own field, so this is a constant: one
#: planning scope per control repository, which is exactly the
#: granularity a pre-record turn has.
PLANNING_OWNER_ID = "control-repository"

#: How many hex characters of the control identity digest ride in a
#: scope name. The digest is a DISAMBIGUATOR, not the credential — the
#: assignment record carries the full control identity and the
#: verification compares against that — so a short prefix is enough to
#: keep two deployments' record spaces apart in one shared base.
CONTROL_DIGEST_CHARS = 16

#: What a scope directory's NAME says about itself. Deliberately NOT
#: called an attribution: `parse_scope` reads a label, and only
#: `validate_assignment` turns a label into an owner.
ScopeIdentity = collections.namedtuple(
    "ScopeIdentity", "owner_type control_digest owner_id unit_id"
)


def control_digest(control_identity):
    """The short digest of a control identity that rides in a name.

    Present because the default base is MACHINE-GLOBAL: two
    deployments under one temp directory can mint the same workflow
    id, and without this they would share a record space — the
    cross-owner contamination Z-1 closed, arriving by a different
    route. The full identity stays in the assignment record and is
    what verification compares.
    """
    if not isinstance(control_identity, str) or not control_identity:
        raise ValueError(
            "a scope requires a control identity; a record space"
            " shared between controls is what Z-1 forbids"
        )
    return hashlib.sha256(
        control_identity.encode("utf-8")
    ).hexdigest()[:CONTROL_DIGEST_CHARS]

ASSIGNMENT_DIR_NAME = "di-scope-assignments"
ASSIGNMENT_KEY_FILE = ".binding-key"
ASSIGNMENT_SUFFIX = ".assignment.json"

#: Every field the binding covers, listed once so that
#: within this module writer and verifier cannot drift apart.
#: A field added on one side and uncovered on the other is a field an
#: attacker may change freely.
ASSIGNMENT_BOUND_FIELDS = (
    "scope_name", "owner_type", "control_digest", "owner_id",
    "unit_id", "control_identity", "assigned_at", "assigned_by_pid",
)

#: Why a directory that LOOKS like a scope is not one. Reported, and
#: left alone within recovery.
UNATTRIBUTED_NO_LABEL = "no owner label in the directory name"
UNATTRIBUTED_NO_ASSIGNMENT = "no assignment record in the store"
UNATTRIBUTED_MALFORMED = "the assignment record is malformed"
UNATTRIBUTED_FORGED = "the assignment integrity binding does not verify"
UNATTRIBUTED_CONFLICTING = (
    "the assignment record names a different owner than the directory"
)
UNATTRIBUTED_STALE = (
    "the assignment names an owner the durable record no longer holds"
)
#: Task 8 R22-2: the CREDENTIAL could not be read — the assignment record,
#: the store holding it, or the store's binding key. Neither missing nor
#: malformed: UNAVAILABLE. Reported, and the scope left alone.
UNATTRIBUTED_CREDENTIAL_UNAVAILABLE = (
    "%s: the assignment credential cannot be read" % OBSERVATION_UNAVAILABLE
)
#: Task 8 R22-2: the DURABLE WORKFLOW RECORD could not be read, so whether
#: a workflow-owned assignment's owner still exists cannot be known — never
#: STALE (which says the record was read and does not hold the owner).
UNATTRIBUTED_OWNERS_UNAVAILABLE = (
    "%s: the durable workflow record cannot be read, so whether this owner"
    " still exists cannot be known" % OBSERVATION_UNAVAILABLE
)


def is_unavailable(reason):
    """Task 8 R22-2: whether ``reason`` reports an observation that could
    not be made (never an absence, never a malformation)."""
    return isinstance(reason, str) and reason.startswith(OBSERVATION_UNAVAILABLE)


class UnavailableOwners(object):
    """Task 8 R22-2: the current owners when the durable workflow record
    could NOT be read — distinct from an empty set (a readable record that
    holds no owner). Not a container: a caller testing membership raises
    rather than reading the unavailable record as "no owners".
    ``source`` and ``reason`` name the observation that failed."""

    __slots__ = ("source", "reason")

    def __init__(self, source, reason):
        self.source = source
        self.reason = reason

    def __repr__(self):
        return "UnavailableOwners(%r, %r)" % (self.source, self.reason)


def scope_name(owner_type, control_identity, owner_id, unit_id):
    """The directory name for one owner. A LABEL, not a credential.

    Every part is REQUIRED. A scope missing one would attribute its
    contents to "some owner", which is the state Z-1 exists to end, so
    this raises rather than falling back to a shared root.
    """
    if owner_type not in OWNER_TYPES:
        raise ValueError(
            "%r is not an owner type; a scope carries an EXACT owner"
            " type, never a shared parseable prefix (AG-5)"
            % (owner_type,)
        )
    digest = control_digest(control_identity)
    for label, value in (("owner id", owner_id), ("unit id", unit_id)):
        if not isinstance(value, str) or not value:
            raise ValueError(
                "a process scope requires a %s; an unattributed scope"
                " is what Z-1 forbids" % label
            )
        if SCOPE_SEPARATOR in value or "/" in value or "=" in value:
            raise ValueError("%r cannot appear in a scope name" % value)
    return "%s-%s%s%s%s%s%s%s%s%s%s%s" % (
        SCOPE_PREFIX, SCOPE_OWNER_KEY, owner_type,
        SCOPE_SEPARATOR, SCOPE_CONTROL_KEY, digest,
        SCOPE_SEPARATOR, SCOPE_ID_KEY, owner_id,
        SCOPE_SEPARATOR, SCOPE_UNIT_KEY, unit_id,
    )


def owner_scope(owner_type, control_identity, owner_id, unit_id,
                base=None):
    """The record root LABELLED for exactly this owner.

    A path; holding it proves nothing within this discipline. The
    spawn path must ASSIGN it (`assign_scope`) before the recovery
    path will act on what is inside it.
    """
    return os.path.join(
        owned_root_base(base),
        scope_name(owner_type, control_identity, owner_id, unit_id),
    )


def workflow_scope(control_identity, workflow_id, task_id, base=None):
    """The record root owned by exactly this workflow and task, under
    exactly this control repository."""
    return owner_scope(
        OWNER_TYPE_WORKFLOW, control_identity, workflow_id, task_id,
        base=base,
    )


def planning_scope(control_identity, base=None):
    """The record root owned by exactly this PRE-RECORD planning turn.

    Its own owner type (AG-5). There is no workflow yet — that is what
    "pre-record" means — and the previous shape said so by prefixing
    the workflow id with "planning-", which put two different kinds of
    owner in one namespace and left the distinction to a substring.
    """
    return owner_scope(
        OWNER_TYPE_PLANNING, control_identity, PLANNING_OWNER_ID,
        PLANNING_UNIT_ID, base=base,
    )


def parse_scope(directory):
    """The `ScopeIdentity` a directory NAME claims, or None.

    A CLAIM. This function is not an attribution and must not be used
    as one: R-43 found the previous code treating this parse as proof
    of ownership, which let anything able to create a directory under
    the base enter destructive enumeration. `validate_assignment` is
    the check that decides whether the claim is true.
    """
    name = os.path.basename(directory.rstrip(os.sep))
    head = "%s-%s" % (SCOPE_PREFIX, SCOPE_OWNER_KEY)
    if not name.startswith(head):
        return None
    parts = name[len(head):].split(SCOPE_SEPARATOR)
    if len(parts) != 4:
        return None
    owner_type, control_part, id_part, unit_part = parts
    if owner_type not in OWNER_TYPES:
        return None
    if not control_part.startswith(SCOPE_CONTROL_KEY):
        return None
    if not id_part.startswith(SCOPE_ID_KEY):
        return None
    if not unit_part.startswith(SCOPE_UNIT_KEY):
        return None
    digest = control_part[len(SCOPE_CONTROL_KEY):]
    owner_id = id_part[len(SCOPE_ID_KEY):]
    unit_id = unit_part[len(SCOPE_UNIT_KEY):]
    if not digest or not owner_id or not unit_id:
        return None
    return ScopeIdentity(owner_type, digest, owner_id, unit_id)


# --- The protected store -------------------------------------------


def assignment_base(base=None):
    """The store the CREDENTIALS live in.

    A SIBLING of the record base, outside it and not inside.
    Enumeration walks that directory, and a credential store sitting
    in the space being enumerated is one rename away from being
    mistaken for a record.
    """
    if base:
        return os.path.join(base, ASSIGNMENT_DIR_NAME)
    return os.path.join(default_base(), ASSIGNMENT_DIR_NAME)


def assignment_path(name, base=None):
    return os.path.join(assignment_base(base), name + ASSIGNMENT_SUFFIX)


def _binding_key(base=None):
    """The store's HMAC key, created ONCE at mode 0600.

    `O_CREAT | O_EXCL` so that within this store two processes cannot
    each install a key and invalidate the other's assignments: the
    loser's create fails and it reads the winner's key.
    """
    directory = assignment_base(base)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, ASSIGNMENT_KEY_FILE)
    try:
        handle_fd = os.open(
            path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError:
        pass
    else:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(secrets.token_bytes(32))
            handle.flush()
            os.fsync(handle.fileno())
    # Task 8 R26-1: an EXISTING key that cannot be read as a record (a FIFO,
    # a device, oversized) RAISES here; it is never replaced, because only
    # the O_EXCL create above ever installs a key.
    key = read_ownership_record(path)
    if len(key) < 32:
        raise OSError(
            "the scope assignment binding key at %s is truncated;"
            " refusing to bind or verify against it" % path
        )
    return key


def _existing_binding_key(base=None):
    """Task 8 R22-A: the store's HMAC key for a PROOF READ — read-only. It
    never creates the directory or the key (``_binding_key`` is the WRITER's
    accessor, and only the writer may install a key): a missing key raises
    ``FileNotFoundError``, an unreadable one its ``OSError`` (Task 8 R26-1:
    ``NotARegularRecord`` / ``OversizedRecord`` among them; never a wait)."""
    return read_ownership_record(os.path.join(assignment_base(base), ASSIGNMENT_KEY_FILE))


#: Task 8 R23-2: the alphabet of a binding as the writer makes it — an
#: HMAC-SHA256 ``hexdigest`` (``_binding_for``).
_BINDING_DIGITS = frozenset("0123456789abcdef")
_BINDING_LENGTH = 64


def _is_binding(value):
    """Whether ``value`` has the REPRESENTATION of a binding: exactly
    ``_BINDING_LENGTH`` lowercase hexadecimal ASCII characters. Nothing else
    can be one, so nothing else is compared."""
    return (isinstance(value, str) and len(value) == _BINDING_LENGTH
            and all(char in _BINDING_DIGITS for char in value))


def _binding_for(record, base=None, key=None):
    """The binding of ``record`` under ``key`` — or, when none is handed in
    (the WRITER, ``assign_scope``), under the store's key, created once if
    absent (``_binding_key``). The proof READ (``read_assignment``) always
    hands in the key it READ (``_existing_binding_key``, R22-A)."""
    payload = json.dumps(
        {field: record.get(field) for field in ASSIGNMENT_BOUND_FIELDS},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _binding_key(base) if key is None else key, payload, hashlib.sha256
    ).hexdigest()


def assign_scope(owner_type, control_identity, owner_id, unit_id,
                 base=None, now=None):
    """Write the ASSIGNMENT, then create the scope. In that order.

    AG-1. The record is durable and atomic (`os.replace` onto a
    fsynced temp file) and it lands BEFORE the spawn, so a crash
    anywhere after this line leaves a scope whose owner is READABLE
    FROM A CREDENTIAL rather than guessable from a name.

    Re-assigning the same scope to the same owner and control identity
    refreshes it. Re-assigning it to a DIFFERENT control identity
    RAISES: two owners claiming one record space is the contamination
    Z-1 closed, and silently overwriting would let the second claim
    inherit the first's records.
    """
    name = scope_name(owner_type, control_identity, owner_id, unit_id)
    existing, _reason = read_assignment(name, base=base)
    if existing is not None and (
        existing.get("control_identity") != control_identity
    ):
        raise ValueError(
            "scope %s is already assigned to control identity %r;"
            " refusing to reassign it to %r"
            % (name, existing.get("control_identity"), control_identity)
        )
    record = {
        "scope_name": name,
        "owner_type": owner_type,
        "control_digest": control_digest(control_identity),
        "owner_id": owner_id,
        "unit_id": unit_id,
        "control_identity": control_identity,
        "assigned_at": time.time() if now is None else now,
        "assigned_by_pid": os.getpid(),
    }
    record["binding"] = _binding_for(record, base=base)
    path = assignment_path(name, base=base)
    temporary = "%s.%s.tmp" % (path, secrets.token_hex(8))
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    scope = owner_scope(
        owner_type, control_identity, owner_id, unit_id, base=base
    )
    os.makedirs(scope, exist_ok=True)
    return scope


def read_assignment(name, base=None):
    """``(record, reason)`` for one scope name. Exactly one is None.

    The binding is verified HERE, so no caller can obtain a record
    that has not been checked.

    Task 8 R22-2: read STRICTLY, and the three answers kept apart. ABSENT
    is ``FileNotFoundError`` alone (``UNATTRIBUTED_NO_ASSIGNMENT``);
    MALFORMED is content that was read and does not decode or does not
    carry a record (``UNATTRIBUTED_MALFORMED``); a record, a store or a
    binding key that could NOT BE READ is UNAVAILABLE
    (``UNATTRIBUTED_CREDENTIAL_UNAVAILABLE`` and what failed) — never
    "missing" (an ``exists`` check that swallowed the error said so) and
    never "malformed".

    Task 8 R24-2: ``open`` raises ``FileNotFoundError`` for an EXISTING
    credential link whose target is unavailable too (or a dangling link among
    its ancestors), so the traversal (``classify_missing``) decides: genuine
    absence is still no assignment; anything else is UNAVAILABLE.
    """
    path = assignment_path(name, base=base)
    try:
        # Task 8 R26-1: never waits; bounded at the credential's own limit.
        raw = read_ownership_record(path, limit=CREDENTIAL_RECORD_BYTES)
    except FileNotFoundError:
        missing = classify_missing(path)
        if missing.availability == READ_ABSENT:
            return None, UNATTRIBUTED_NO_ASSIGNMENT
        return None, "%s (the assignment record: %s)" % (
            UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, missing.problem)
    except IsADirectoryError:
        return None, UNATTRIBUTED_MALFORMED     # an entry that is not a record
    except OSError as exc:
        return None, "%s (the assignment record: %s)" % (
            UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, exc.__class__.__name__)
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):        # R23-2: nesting is content too
        return None, UNATTRIBUTED_MALFORMED
    if not isinstance(record, dict):
        return None, UNATTRIBUTED_MALFORMED
    for field in ASSIGNMENT_BOUND_FIELDS:
        if record.get(field) is None:
            return None, UNATTRIBUTED_MALFORMED
    presented = record.get("binding")
    # Task 8 R23-2: the binding's REPRESENTATION is validated before any
    # comparison. Only what the writer makes is a binding; anything else —
    # non-ASCII, an escaped surrogate, the wrong length or alphabet — is
    # MALFORMED here, never handed to ``hmac.compare_digest`` (which RAISES on
    # a non-ASCII str) and never FORGED.
    if not _is_binding(presented):
        return None, UNATTRIBUTED_MALFORMED
    # Task 8 R22-A: the key is READ, never created — a proof read writes
    # nothing. A key that is missing, unreadable or truncated makes the proof
    # UNAVAILABLE; FORGED is reserved for a binding that fails against a key
    # that was present and read.
    try:
        key = _existing_binding_key(base)
    except FileNotFoundError:
        return None, "%s (the store's binding key: missing)" % (
            UNATTRIBUTED_CREDENTIAL_UNAVAILABLE)
    except OSError as exc:
        return None, "%s (the store's binding key: %s)" % (
            UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, exc.__class__.__name__)
    if len(key) < 32:
        return None, "%s (the store's binding key: truncated)" % (
            UNATTRIBUTED_CREDENTIAL_UNAVAILABLE)
    try:
        expected = _binding_for(record, key=key)
    except (RecursionError, TypeError, ValueError):
        # R23-2: the binding is computed over UNVERIFIED content; a bound field
        # that cannot be serialized (nested past the encoder's depth) is
        # malformed content, contained here — never an exception that aborts
        # the reader's caller.
        return None, UNATTRIBUTED_MALFORMED
    if not hmac.compare_digest(presented, expected):
        return None, UNATTRIBUTED_FORGED
    return record, None


def validate_assignment(directory, base=None, current_owners=None):
    """``(identity, reason)`` — the AG-3 gate every action passes.

    Exactly one of the two is None. Missing, malformed, forged,
    CONFLICTING or STALE all resolve the same way: the caller reports
    and leaves the directory alone.

    ``current_owners`` is the DURABLE WORKFLOW RECORD's view of who
    exists NOW, as ``(owner_type, owner_id, unit_id)`` triples. Passed,
    a WORKFLOW-owned assignment naming an owner absent from it is
    STALE. Not passed, staleness is UNCHECKED and this says so rather
    than implying the check happened: the protected-store checks still
    run, and a caller holding a durable workflow record is expected to
    supply it. Task 8 R22-2: passed as ``UnavailableOwners`` (the record
    could not be read), a WORKFLOW-owned assignment is
    ``UNATTRIBUTED_OWNERS_UNAVAILABLE`` — never STALE.

    The staleness gate covers WORKFLOW owners only, and the reason is
    exact: within this ordering a planning scope exists BEFORE any
    workflow record — that is what "pre-record" means — so the
    workflow record is not its authority and its absence there is
    uninformative. A planning
    assignment is validated against the protected store alone, which
    is stated here rather than left as an unexplained exemption.
    """
    claimed = parse_scope(directory)
    if claimed is None:
        return None, UNATTRIBUTED_NO_LABEL
    name = os.path.basename(directory.rstrip(os.sep))
    record, reason = read_assignment(name, base=base)
    if record is None:
        return None, reason
    assigned = ScopeIdentity(
        record["owner_type"], record["control_digest"],
        record["owner_id"], record["unit_id"],
    )
    if record["scope_name"] != name or assigned != claimed:
        return None, UNATTRIBUTED_CONFLICTING
    # The digest in the NAME is a label; the full control identity in
    # the RECORD is what it must agree with. Checking only the label
    # would let a record name one control and be filed under another.
    try:
        if control_digest(record["control_identity"]) != (
            record["control_digest"]
        ):
            return None, UNATTRIBUTED_CONFLICTING
    except ValueError:
        return None, UNATTRIBUTED_MALFORMED
    if (
        isinstance(current_owners, UnavailableOwners)
        and assigned.owner_type == OWNER_TYPE_WORKFLOW
    ):
        # Task 8 R22-2: the durable record could not be read — whether this
        # owner exists is UNAVAILABLE, never STALE, and nothing is inferred.
        return None, UNATTRIBUTED_OWNERS_UNAVAILABLE
    if (
        current_owners is not None
        and not isinstance(current_owners, UnavailableOwners)
        and assigned.owner_type == OWNER_TYPE_WORKFLOW
        and tuple(assigned) not in {
            tuple(owner) for owner in current_owners
        }
    ):
        return None, UNATTRIBUTED_STALE
    return assigned, None


# --- Enumeration and recovery ---------------------------------------


def _scope_directories(base=None):
    """Task 8 R21-2: ``(directories, unavailable)`` — every scope directory
    under the base, read STRICTLY (``FileNotFoundError`` alone is absence;
    ``stat`` follows a link exactly as the ``isdir`` guard it replaces did),
    with every observation that could not be made as ``(path, reason)``: a
    base that cannot be examined or listed, an entry that cannot be
    examined. Never "no scopes" for a base it could not read.

    Task 8 R24-2: ``FileNotFoundError`` is absence only when the traversal
    says so (``_observed_missing``); a dangling link at the base or at an
    entry — or among their ancestors — is reported, never "no scopes" and
    never a scope gone meanwhile."""
    prefix = owned_root_base(base)
    try:
        mode = os.stat(prefix).st_mode
    except FileNotFoundError:
        gap = _observed_missing(prefix, "the scope base")
        return [], ([] if gap is None else [gap])
    except OSError as exc:
        return [], [_unavailable(prefix, "the scope base", exc)]
    if not stat.S_ISDIR(mode):
        return [], [(prefix, "%s: the scope base is not a directory"
                     % OBSERVATION_UNAVAILABLE)]
    try:
        names = sorted(os.listdir(prefix))
    except OSError as exc:
        return [], [_unavailable(prefix, "the scope base", exc)]
    directories, unavailable = [], []
    for name in names:
        directory = os.path.join(prefix, name)
        try:
            mode = os.stat(directory).st_mode
        except FileNotFoundError:
            gap = _observed_missing(directory, "the scope")
            if gap is not None:
                unavailable.append(gap)
            continue                   # gone meanwhile (genuinely), or reported
        except OSError as exc:
            unavailable.append(_unavailable(directory, "the scope", exc))
            continue
        if stat.S_ISDIR(mode):
            directories.append(directory)
    return directories, unavailable


def classify_scopes(base=None, current_owners=None, unavailable=None):
    """``(attributed, unattributed)`` for every directory under the
    base — ONE walk, so that within this result the two lists cannot
    disagree.

    ``attributed`` is ``(identity, directory)``; ``unattributed`` is
    ``(directory, reason)``. Every directory lands in exactly one.

    Task 8 R21-2: an observation the walk could not make is REPORTED —
    into ``unavailable`` when the caller passes a list, else as
    ``ObservationUnavailable`` — never dropped as if absent. R22-2: so is
    a scope whose CREDENTIAL, or whose owner's durable record, could not be
    read — it stays UNATTRIBUTED with that unavailable reason (left alone,
    nothing inferred) and is counted with the observations not made.
    """
    attributed, unattributed = [], []
    directories, missing = _scope_directories(base)
    if missing:
        if unavailable is None:
            raise ObservationUnavailable(missing)
        unavailable.extend(missing)
    credential_gaps = []
    for directory in directories:
        identity, reason = validate_assignment(
            directory, base=base, current_owners=current_owners
        )
        if identity is None:
            unattributed.append((directory, reason))
            if is_unavailable(reason):
                credential_gaps.append((directory, reason))
        else:
            attributed.append((identity, directory))
    if credential_gaps:
        if unavailable is None:
            raise ObservationUnavailable(credential_gaps)
        unavailable.extend(credential_gaps)
    return attributed, unattributed


def attributed_scopes(base=None, current_owners=None):
    """Every scope carrying a VALID ASSIGNMENT, with its owner.

    THE ENUMERATION RECOVERY IS ALLOWED TO ACT ON. The assignment is
    read, and within this gate the basename never is (AG-2): a directory whose name
    parses but whose assignment is missing, malformed, forged,
    conflicting or stale is not in this result at all.
    """
    return classify_scopes(base, current_owners=current_owners)[0]


def unattributed_report(base=None, current_owners=None):
    """``(directory, reason)`` for everything left alone, WITH THE
    REASON: within such a report a bare "unattributed" cannot
    distinguish a stray directory from a forgery attempt."""
    return classify_scopes(base, current_owners=current_owners)[1]


def unattributed_entries(base=None, current_owners=None):
    """The directories that carry no valid assignment. Reported and
    left alone: acting on one would be the guess the whole ownership
    discipline forbids."""
    return [
        directory
        for directory, _reason in unattributed_report(
            base, current_owners=current_owners
        )
    ]


def scope_has_live_group(directory):
    """Whether a group recorded under ``directory`` is still OURS and
    alive.

    A PREDICATE, and deliberately only that. Retiring a scope means
    deleting the evidence a later run would recover its processes
    from, so the liveness question is answered here — beside the
    recovery that asks it — while the removal stays with the caller
    that wants it. Production has no scope-retirement path yet; the
    consequence is stated rather than papered over: an assignment is
    written before every spawn, and within this module nothing retires
    one, so the store grows until a caller outside it prunes.

    Task 8 R21-2: a root, prefix or group record that cannot be read
    answers True — it may be live, and a predicate that gates a removal
    must never read an unreadable record as gone.
    """
    roots, missing = owned_roots_observed(directory)
    if missing:
        return True
    groups, ledger_gap = ledger_groups(directory)    # Task 8 R27-1
    for root, pgid in roots:
        if pgid is not None and ledger_gap is not None:
            return True                # R27-1: an owner ledger NOT OBSERVED may hide ours
        if pgid is not None and ledger_contradicts(groups, root, pgid):
            return True                # R27-1: contradictory records may be ours
        if pgid is None or pgid <= 1 or not _group_alive(pgid):
            continue
        # AR-3: a live number is not a live process of ours.
        try:
            ours, _reason = group_is_ours(root)
        except (OSError, UnicodeDecodeError):
            return True                # R21-B: unreadable corroboration may be ours

        if ours is not None or _reason in LEADER_UNAVAILABLE_REASONS:
            return True                # R22-1: a failed leader query may be ours too
        if _reason == UNCORROBORATED_START_FRAGMENT:
            return True                # R27-1: this leader's start, cut short
    return False


#: Why a scope was NOT retired. Reported, and the scope is left.
RETIRE_REFUSED_LIVE_GROUP = (
    "a corroborated group recorded in this scope is still running"
)
RETIRE_REFUSED_UNATTRIBUTED = (
    "the scope carries no valid assignment naming this workflow"
)
#: Task 8 R20-1: the evidence a retirement would otherwise delete while a
#: process recorded in it may still live, or while it cannot be read.
RETIRE_REFUSED_LEADERLESS = (
    "a group recorded in this scope is alive and its ownership cannot be"
    " corroborated (its recorded leader is gone; its descendants survive)"
)
RETIRE_REFUSED_UNSTAMPED = (
    "an owned root in this scope was never stamped with a process group,"
    " so whether its process started cannot be known"
)
RETIRE_REFUSED_UNREADABLE = (
    "this scope's ownership records cannot be read"
)
#: Task 8 R27-1: records that CONTRADICT each other — the owner ledger names a
#: different group for a root than the root's own record, or a live leader's
#: recorded start time is cut short — so absence cannot be established.
RETIRE_REFUSED_CONTRADICTED = (
    "this scope's ownership records contradict each other (the owner ledger"
    " names a different group, or a recorded start time is cut short), so"
    " absence cannot be established"
)
#: Task 8 R20-B (Addendum C, C-2): a removal that was ATTEMPTED but not
#: OBSERVED. Retirement now carries settlement and prunability authority, so
#: "retired" means the directory and its credential were seen gone afterwards.
RETIRE_REFUSED_UNDELETED = (
    "this scope's directory survived its removal"
)
RETIRE_REFUSED_CREDENTIAL_KEPT = (
    "this scope's assignment credential could not be removed"
)
#: C-2b: a credential whose NAME claims this workflow, its directory gone,
#: that cannot be read or does not verify — ambiguous, never absent; kept.
RETIRE_REFUSED_CREDENTIAL_UNREAD = (
    "an assignment credential naming this workflow cannot be read or does not"
    " verify, so its absence cannot be established; it is left in place"
)
#: The largest process-group id a record may name (``pid_t`` is a signed
#: 32-bit integer); a larger number is not a group id.
MAX_RECORDED_GROUP_ID = 2 ** 31 - 1


def _recorded_group_id(raw):
    """The group id an owned root's group record names — ASCII digits
    only (the child-side stamp writes ``str(group)``), in ``2 ..
    MAX_RECORDED_GROUP_ID`` — or None for anything else. Never raises."""
    text = raw.strip()
    if not text or len(text) > len(str(MAX_RECORDED_GROUP_ID)) or not all(
            48 <= byte <= 57 for byte in text):
        return None
    value = int(text)
    return value if 1 < value <= MAX_RECORDED_GROUP_ID else None


def retirement_refusal(directory):
    """Task 8 R20-1: why ``directory``'s ownership records must NOT be
    retired — or None when ABSENCE is established: every owned root is
    stamped with a process group that is gone, or whose id now names a
    DIFFERENT leader (a reused id: the recorded group is gone).

    Each reason RETAINS the scope (reported and left, so a later terminal
    cleanup or recovery still finds it): a CORROBORATED group still
    running (``RETIRE_REFUSED_LIVE_GROUP``, AR-3); a group alive whose
    ownership cannot be corroborated because its recorded leader is gone
    and descendants survive (``RETIRE_REFUSED_LEADERLESS`` — never
    signalled, and never read as gone); a root never stamped
    (``RETIRE_REFUSED_UNSTAMPED`` — a spawn whose group was never
    recorded); records that cannot be read (``RETIRE_REFUSED_UNREADABLE``
    — an unreadable record is not an absent one, and ambiguity is not
    absence).

    Read with calls that RAISE (``lstat``, ``listdir``, ``open``), so that
    ``FileNotFoundError`` alone reads as absent. (Before Task 8 R21-2,
    ``owned_roots``' ``isdir`` guards and parse fallback turned an unreadable
    prefix or group record into "no roots" or "unstamped"; it now reports
    them unavailable, but this reader keeps its own ``lstat``, which does not
    follow a link, and refuses outright.) It is the rule the R19-3
    verification reader applies to the verification scope
    (``verification.prior_ownership``), applied here to every scope a
    retirement removes. Reads only: nothing is signalled or removed.

    Task 8 R27-1: a group record or start time read here is one a stamp wrote
    WHOLE — the stamp writer replaces each record atomically
    (``spawn_stamp._write_record``) — so a recorded group observed gone, or a
    start time that differs, is the recorded process gone or its id reused,
    never a fragment a failed parent confirmation left behind. For records an
    earlier, truncating writer may have left, the scope's owner ledger is read
    (``ledger_groups``) in three branches that are never collapsed:
    - GENUINELY ABSENT (no ledger, no row for the root's spawn): evidence of
      nothing — the root is read exactly as before (the parent-death and
      child-only recovery properties);
    - PRESENT but NOT OBSERVED (not a regular file, oversized, unreadable,
      unresolvable, not UTF-8): ``RETIRE_REFUSED_UNREADABLE`` once any root is
      stamped — never settled while the observation is unavailable;
    - READABLE and naming a DIFFERENT group for a root's spawn
      (``ledger_contradicts``, live or not): ``RETIRE_REFUSED_CONTRADICTED``.
    And a live leader whose recorded start is a strict PREFIX of its own
    (``UNCORROBORATED_START_FRAGMENT``: cut short, never reuse):
    ``RETIRE_REFUSED_CONTRADICTED``. The stated RESIDUAL: a legacy group-record
    fragment whose ledger is genuinely absent, or carries no row for its
    spawn, is read as it is — its true group is not recorded anywhere."""
    prefix = owned_root_base(directory)
    try:
        mode = os.lstat(prefix).st_mode
    except FileNotFoundError:
        # Task 8 R24-2: ``lstat`` spares only the FINAL component. Through a
        # dangling ANCESTOR — the scope entry itself, become a link after
        # ``_matching_scopes`` attributed it — it raises FileNotFoundError
        # too. "No owned roots" only when the traversal says the prefix is
        # GENUINELY missing; anything else is an ownership observation not
        # made, so the scope is REFUSED: never retired, its credential (the
        # sole deletion proof) never removed.
        if classify_missing(prefix).availability == READ_ABSENT:
            return None                # the scope holds no owned roots
        return RETIRE_REFUSED_UNREADABLE
    except OSError:
        return RETIRE_REFUSED_UNREADABLE
    if not stat.S_ISDIR(mode):
        return RETIRE_REFUSED_UNREADABLE
    try:
        names = sorted(os.listdir(prefix))
    except OSError:
        return RETIRE_REFUSED_UNREADABLE
    roots, (groups, ledger_gap) = [], ledger_groups(directory)     # Task 8 R27-1
    for name in names:
        root = os.path.join(prefix, name)
        try:
            mode = os.lstat(root).st_mode
        except OSError:
            return RETIRE_REFUSED_UNREADABLE
        if stat.S_ISREG(mode):
            continue                   # not a root (the prefix's freeze file, for one)
        if not stat.S_ISDIR(mode):
            return RETIRE_REFUSED_UNREADABLE
        record = os.path.join(root, OWNED_ROOT_PGID_FILE)
        try:
            raw = read_ownership_record(record)     # Task 8 R26-1: never waits
        except FileNotFoundError:
            # Task 8 R24-2: never stamped only when the record is GENUINELY
            # missing; a dangling record link is a record that cannot be read.
            if classify_missing(record).availability == READ_ABSENT:
                return RETIRE_REFUSED_UNSTAMPED
            return RETIRE_REFUSED_UNREADABLE
        except OSError:
            return RETIRE_REFUSED_UNREADABLE
        pgid = _recorded_group_id(raw)
        if pgid is None:
            return RETIRE_REFUSED_UNREADABLE
        if ledger_gap is not None:
            return RETIRE_REFUSED_UNREADABLE       # Task 8 R27-1: present, NOT observed
        if ledger_contradicts(groups, root, pgid):
            return RETIRE_REFUSED_CONTRADICTED     # Task 8 R27-1: live or not
        roots.append((root, pgid))
    for root, pgid in roots:
        if not _group_alive(pgid):
            continue
        try:
            ours, why = group_is_ours(root)
        except (OSError, ValueError):  # e.g. an undecodable nonce or start record
            return RETIRE_REFUSED_UNREADABLE
        if ours is not None:
            return RETIRE_REFUSED_LIVE_GROUP
        if why == UNCORROBORATED_START_MISMATCH:
            continue
        if why == UNCORROBORATED_START_FRAGMENT:
            return RETIRE_REFUSED_CONTRADICTED     # Task 8 R27-1: cut short, never reuse
        if why in LEADER_UNAVAILABLE_REASONS:
            return RETIRE_REFUSED_UNREADABLE       # R22-1: a failed query, not a gone leader
        return RETIRE_REFUSED_LEADERLESS
    return None


def workflow_scopes(control_identity, workflow_id, base=None):
    """Every scope whose ASSIGNMENT names exactly this owner.

    The assignment decides, within this selection, and the name does
    not (AG-2): a directory whose basename happens to parse into this
    workflow id is not this workflow's, and retirement is a removal,
    so the credential is what selects.
    """
    digest = control_digest(control_identity)
    found = []
    directories, missing = _scope_directories(base)
    if missing:
        raise ObservationUnavailable(missing)      # R21-2: never "no scopes"
    for directory in directories:
        identity, _reason = validate_assignment(directory, base=base)
        if identity is None:
            continue
        if identity.owner_type != OWNER_TYPE_WORKFLOW:
            continue
        if identity.control_digest != digest:
            continue
        if identity.owner_id != workflow_id:
            continue
        found.append((identity, directory))
    return found


def owned_scope_refusals(control_identity, workflow_id, base=None, skip_units=()):
    """Task 8 R20-2: ``(refusals, problem)`` for EVERY scope this workflow
    owns, read STRICTLY, with ``refusals`` as ``(directory, reason)`` for
    each whose records must be KEPT:

    - a scope whose assignment names exactly this workflow and control
      repository, and whose absence ``retirement_refusal`` cannot
      establish (a corroborated or leaderless live group, an unstamped
      root, unreadable records) — the reason retirement itself would give;
    - a directory whose NAME claims this workflow and control repository
      while its assignment does not validate — ambiguous ownership, never
      read as "not ours" (``RETIRE_REFUSED_UNATTRIBUTED``, with the reason);
    - an entry whose NAME claims this workflow and control repository but
      which is not a directory — a symbolic link, file, FIFO, socket or
      device, never read as absent (``RETIRE_REFUSED_UNREADABLE``, naming
      what it is; Addendum A).

    ``problem`` is ``RETIRE_REFUSED_UNREADABLE`` when the scopes themselves
    cannot be enumerated (an unreadable record is not an absent one), else
    None. ``skip_units`` names units another strict reader owns (the
    Broker passes the verification unit, which ``verification
    .prior_ownership`` reads). Reads only: nothing is signalled or
    removed. Enumerated by ``_matching_scopes`` — the SAME strict
    enumeration the retirement reads (Addendum C, C-2c)."""
    attributed, refusals, problem = _matching_scopes(
        control_identity, workflow_id, base=base, skip_units=skip_units)
    if problem is not None:
        return [], problem
    for _identity, directory in attributed:
        reason = retirement_refusal(directory)
        if reason is not None:
            refusals.append((directory, reason))
    return sorted(refusals), None


def _matching_scopes(control_identity, workflow_id, base=None, skip_units=()):
    """Task 8 R20-B (Addendum C, C-2c): ``(attributed, refusals, problem)``
    for every entry under the owned-root base whose NAME claims exactly this
    workflow and control repository — the ONE enumeration the hold
    (``owned_scope_refusals``) and the retirement (``retire_workflow_scopes``)
    both read, so they never classify the same evidence differently.

    Read with calls that RAISE (``lstat``, ``listdir``), so
    ``FileNotFoundError`` alone reads as absent. (Before Task 8 R21-2,
    ``_scope_directories``' ``isdir`` guard read an unreadable base as "no
    scopes" and dropped an entry it could not examine without trace; it now
    reports both unavailable, but follows a link with ``stat``, so this
    enumeration keeps its own ``lstat``.)

    ``attributed`` — ``(identity, directory)`` whose ASSIGNMENT validates
    (``validate_assignment``): the ONLY scopes a deletion may ever act on
    (AG-2/AG-3). ``refusals`` — ``(directory, reason)`` for a NAME that claims
    this workflow while it is not a directory (``RETIRE_REFUSED_UNREADABLE``,
    naming what it is; Addendum A), cannot be examined
    (``RETIRE_REFUSED_UNREADABLE``) or carries no assignment that validates
    (``RETIRE_REFUSED_UNATTRIBUTED``, with the reason). A name grants
    RETENTION only, never deletion. ``problem`` is
    ``RETIRE_REFUSED_UNREADABLE`` when the base cannot be enumerated, else
    None. Reads only."""
    try:
        digest = control_digest(control_identity)
    except ValueError:
        return [], [], RETIRE_REFUSED_UNREADABLE
    prefix = owned_root_base(base)
    try:
        mode = os.lstat(prefix).st_mode
    except FileNotFoundError:
        # Task 8 R24-2: ``lstat`` of the base itself raises it through a
        # dangling ANCESTOR link too; only the traversal establishes that no
        # scope was ever assigned under this base.
        if classify_missing(prefix).availability == READ_ABSENT:
            return [], [], None        # no scope was ever assigned under this base
        return [], [], RETIRE_REFUSED_UNREADABLE
    except OSError:
        return [], [], RETIRE_REFUSED_UNREADABLE
    if not stat.S_ISDIR(mode):
        return [], [], RETIRE_REFUSED_UNREADABLE
    try:
        names = sorted(os.listdir(prefix))
    except OSError:
        return [], [], RETIRE_REFUSED_UNREADABLE
    attributed, refusals = [], []
    for name in names:
        directory = os.path.join(prefix, name)
        claimed = parse_scope(directory)
        if (claimed is None or claimed.owner_type != OWNER_TYPE_WORKFLOW
                or claimed.control_digest != digest or claimed.owner_id != workflow_id
                or claimed.unit_id in skip_units):
            continue
        try:
            mode = os.lstat(directory).st_mode
        except OSError:
            refusals.append((directory, RETIRE_REFUSED_UNREADABLE))
            continue
        if not stat.S_ISDIR(mode):
            # The base's own files never reach here (the match filter above
            # excludes them): this NAME claims exactly this workflow and
            # control repository, yet the entry is a symbolic link (lstat
            # does not follow it, even to a live scope), a file, FIFO,
            # socket or device. A name is a claim, never read as absent:
            # UNREADABLE, the bar the base above and ``verification
            # ._scope_roots`` apply to the same condition. Not
            # UNATTRIBUTED — ``validate_assignment`` never stats the entry,
            # so a link can carry a valid assignment.
            refusals.append((directory, "%s (%s, not a directory)" % (
                RETIRE_REFUSED_UNREADABLE, "a symbolic link" if stat.S_ISLNK(mode)
                else "a regular file" if stat.S_ISREG(mode) else "a special file")))
            continue
        identity, reason = validate_assignment(directory, base=base)
        if identity is None:
            refusals.append((directory, "%s (%s)" % (RETIRE_REFUSED_UNATTRIBUTED, reason)))
            continue
        attributed.append((identity, directory))
    return attributed, refusals, None


def retire_workflow_scopes(control_identity, workflow_id, base=None, admit=None):
    """Reclaim this workflow's process-scope records. R-54 AR-4.

    THE LIFECYCLE AL-4..AL-7 DECIDED, which within production nothing
    executed. An assignment is written before every spawn, and until
    this existed no code retired one, so the store grew for the life
    of the machine. A decided policy performed by no code is, within
    production, the same defect as an unenforced value — R-42's class,
    and this is the instance that closes it.

    THE BOUND IS THE WORKFLOW, and within this policy it is never a
    clock (AL-7). A record is reclaimed as part of ITS OWN workflow's
    terminal cleanup, under the assignment credential AG-1/AG-3
    require. Within it, age and size and resemblance are not grounds
    to remove. A clock is not a credential.

    REFUSES unless ABSENCE is established (``retirement_refusal``, Task 8
    R20-1): while a CORROBORATED group recorded in the scope is still
    running (AR-3), while a group whose recorded leader is gone survives
    in its descendants, while a root was never stamped, and while the
    records cannot be read. The record is the only evidence a later run
    could recover that process from, and removing it while the process
    may live is the leak this module exists to prevent. Refused scopes
    are REPORTED and left, so the next terminal cleanup retries — the
    same retain-and-retry shape AC-3 uses.

    RETIRED MEANS OBSERVED GONE (Task 8 R20-B, Addendum C, C-2): a scope
    counts as retired only when, AFTER its removal, its directory and its
    credential are both seen absent (``lstat`` raising FileNotFoundError).
    A directory that survived its removal is REFUSED
    (``RETIRE_REFUSED_UNDELETED``), naming whether its credential went; a
    credential that could not be removed is REFUSED
    (``RETIRE_REFUSED_CREDENTIAL_KEPT``) — a truthful partial effect — and
    is re-attempted by every later retirement of this workflow (a credential
    whose directory is already gone, selected by the record it holds), so a
    surviving credential never reads as retired; one whose name claims this
    workflow but that cannot be read or does not verify is REFUSED and left
    in place (``RETIRE_REFUSED_CREDENTIAL_UNREAD``, C-2b).

    THE DELETION BOUNDARY (Addendum C, C-1b; Task 8 R25-1): ``admit``, when
    given, is the CALLER's admission, taken FRESH for EACH object, after that
    object's own reads, and HELD across its effect: ``admit(effect)`` runs
    ``effect`` only while admitted and returns ``(refusal, result)`` — the
    refusal's text, or None and the effect's result. This module holds no
    authority of its own to consult: the caller supplies it (the Broker's
    Mission cleanup admission, held under the Mission store lock). A refusal
    STOPS the retirement: the objects already removed stay reported as
    retired (a truthful partial effect, never replayed), and this object and
    every one after it are REFUSED with the admission's text, untouched.

    BOTH PROOFS AT THE MOMENT OF EFFECT (Task 8 R25-1). Admission blocks and
    may read, and so do the ownership readers (credential validation, owned
    roots, group liveness). Re-reading either after the other only moves the
    stale proof, so neither is ordered after the other. Instead, per object:

    1. The ownership readers run BEFORE the admission, bracketed by the
       evidence they read: ``_ownership_evidence`` for a scope,
       ``_credential_evidence`` for a credential. The FIRST evidence read
       GATES the readers (Task 8 R26-1): evidence it could not bind refuses
       the object before any reader opens a record it already disproves.
       (The readers themselves read every record through
       ``read_ownership_record``, which never waits either.) Evidence that
       cannot be bound at the second read refuses too, and so does a change
       STILL VISIBLE at the second read. The bracket is two endpoint
       observations, and it binds no interval: a change that reverts
       between them is not seen.
    2. The admission is then taken and HELD.
    3. Inside it, before any effect, the evidence is re-read by the same
       local, non-waiting, BOUNDED reader (``_ownership_evidence``: opens a
       FIFO cannot stall, what was opened re-checked through ``fstat``,
       bounded block reads). It must equal the bound evidence of step 1;
       otherwise the object is refused.

    Unchanged evidence keeps the readers' verdict (``_ownership_evidence``
    argues why, and states the reader's limits). No Mission write that
    takes the store lock — every current Mission save does — can land while
    the admission is held (``admit_cleanup_held``). So at the re-read, both
    proofs are current. A refusal at any step means no credential removal
    and no deletion; the object is retained and retried.

    THE RESIDUAL WINDOW (a limit, not a guarantee): between that re-read
    and the effects (``_remove_credential``, then ``rmtree``) no wait
    intervenes and the Mission store lock is held. But they are not one
    atomic step, and a concurrent filesystem writer is not excluded by any
    lock this module holds.

    NO COMPLETENESS BY OMISSION (Addendum C, C-2c): the scopes are
    enumerated by ``_matching_scopes`` — the hold's own strict enumeration,
    with calls that raise — so a base that cannot be enumerated is REFUSED
    (never "no scopes"), and an entry whose NAME claims this workflow but
    that cannot be examined, is not a directory, or carries no assignment
    that validates (a credential gone bad after the hold) is REFUSED with
    its reason — never dropped from both lists. A name only RETAINS:
    deletion still acts on validated assignments alone (AG-2/AG-3).

    Returns ``(retired, refused)`` where ``refused`` carries
    ``(path, reason)``: a scope directory, or a credential.
    """
    import shutil
    retired = []
    scopes, refused, problem = _matching_scopes(control_identity, workflow_id, base=base)
    if problem is not None:
        # R20-2: scopes that cannot be enumerated are kept and REPORTED,
        # never a raise out of a release that already relinquished.
        return [], [(owned_root_base(base), problem)]
    def admitted(effect):
        """Task 8 R25-1: ONE object's effect under the caller's admission,
        HELD across it. ``admit(effect)`` runs ``effect`` only when admitted
        and returns ``(refusal, result)``. With no admission (legacy and
        non-Mission releases), the effect runs directly."""
        if admit is None:
            return None, effect()
        return admit(effect)

    stopped = None
    for identity, directory in scopes:
        if stopped is not None:
            refused.append((directory, stopped))
            continue
        name = os.path.basename(directory.rstrip(os.sep))
        credential = assignment_path(name, base=base)
        # Task 8 R25-1: BOTH proofs at the moment of effect. The ownership
        # readers block (credential validation, roots, group liveness), so
        # they run here, BEFORE the admission, bracketed by the evidence they
        # read. Evidence that cannot be bound refuses, as does a change still
        # visible at the second read (an endpoint bracket). The admission is
        # then HELD (``admit``) across the effect, and inside it the evidence
        # is re-read by the same bounded, non-waiting reader — the last
        # observation before the removal (the residual window: docstring).
        before = _ownership_evidence(directory, credential, base)
        # Task 8 R26-1: the check GATES the readers; it does not trail them.
        # Evidence the bounded reader could not bind already disproves the
        # records the readers below would open, so it refuses HERE, before
        # any of them runs, as RETIRE_REFUSED_UNREADABLE: the verdict
        # ``retirement_refusal`` gives a record it cannot read. (A credential
        # that becomes unobservable only after the scope was enumerated was
        # reported UNATTRIBUTED, credential unavailable, by its reader
        # before; it is refused as unreadable now. Either way it is
        # unavailable, never absent.)
        if _unbound_reason(before) is not None:
            refused.append((directory, RETIRE_REFUSED_UNREADABLE))
            continue
        current, why = validate_assignment(directory, base=base)
        reason = retirement_refusal(directory) if current is not None else None
        observed = _ownership_evidence(directory, credential, base)
        if current is None:
            refused.append((directory, "%s (%s)" % (RETIRE_REFUSED_UNATTRIBUTED, why)))
            continue
        if tuple(current) != tuple(identity):
            refused.append((directory, "%s (its assignment now names another owner)"
                            % RETIRE_REFUSED_UNATTRIBUTED))
            continue
        if reason is not None:
            refused.append((directory, reason))
            continue
        unbound = _unbound_reason(observed)
        if unbound is not None:
            refused.append((directory, "%s (its ownership evidence cannot be bound: %s)"
                            % (RETIRE_REFUSED_UNREADABLE, unbound)))
            continue
        if observed != before:
            refused.append((directory, "%s (its ownership evidence changed while it was"
                            " read)" % RETIRE_REFUSED_UNREADABLE))
            continue

        def remove_scope(directory=directory, credential=credential, observed=observed):
            # ``observed`` is bound (refused above otherwise), so equality
            # binds the re-read too.
            if _ownership_evidence(directory, credential, base) != observed:
                return ("%s (its ownership evidence changed before the removal)"
                        % RETIRE_REFUSED_UNREADABLE)
            # The ASSIGNMENT goes first. It is the credential, and a scope
            # directory left behind without one is reported as
            # UNATTRIBUTED and left alone — which is the safe residue. The
            # reverse order would leave a credential pointing at a
            # directory that no longer exists, and a later run would have
            # to decide what that means (the credential loop below does).
            kept = _remove_credential(credential)
            shutil.rmtree(directory, ignore_errors=True)
            absence = _absence_unobserved(directory)
            if absence is not None:
                # Task 8 R25-2: a removal whose absence cannot be OBSERVED is
                # retained, and says so — never "survived", never "retired".
                return "%s (%sits assignment credential %s)" % (
                    RETIRE_REFUSED_UNDELETED,
                    "" if absence == STILL_PRESENT else absence + "; ",
                    "was removed" if kept is None else "was kept: %s" % kept)
            if kept is not None:
                return "%s (%s; its directory was removed)" % (
                    RETIRE_REFUSED_CREDENTIAL_KEPT, kept)
            return None
        stopped, outcome = admitted(remove_scope)
        if stopped is not None:
            refused.append((directory, stopped))
            continue
        if outcome is not None:
            refused.append((directory, outcome))
        else:
            retired.append(directory)
    if stopped is not None:
        return retired, refused        # nothing further is removed
    handled = set(os.path.basename(path.rstrip(os.sep)) for path, _reason in refused)
    credentials, unread, problem = _dangling_credentials(control_identity, workflow_id,
                                                         base=base)
    if problem is not None:
        refused.append((assignment_base(base), problem))
    refused.extend((path, reason) for name, path, reason in unread if name not in handled)
    for name, path in credentials:
        if name in handled:
            continue                   # attempted above, in this same retirement
        if stopped is not None:
            refused.append((path, stopped))
            continue
        scope = os.path.join(owned_root_base(base), name)
        # Task 8 R25-1: as for a scope. The selection (its record verifying
        # for this workflow, its scope directory GENUINELY absent) is re-read
        # BEFORE the admission, bracketed by its evidence. The admission is
        # HELD across the removal, and the evidence is re-read inside it.
        before = _credential_evidence(path, scope, base)
        if _unbound_reason(before) is not None:    # Task 8 R26-1: gates the readers
            refused.append((path, RETIRE_REFUSED_UNREADABLE))
            continue
        record, why = read_assignment(name, base=base)
        gone = not _present(scope)
        observed = _credential_evidence(path, scope, base)
        if record is None:
            if why == UNATTRIBUTED_NO_ASSIGNMENT and not _present(path):
                continue               # genuinely gone, as ``_dangling_credentials`` skips it
            refused.append((path, "%s (%s)" % (RETIRE_REFUSED_CREDENTIAL_UNREAD, why)))
            continue
        if (record.get("owner_type"), record.get("owner_id"),
                record.get("control_identity")) != (
                    OWNER_TYPE_WORKFLOW, workflow_id, control_identity):
            refused.append((path, "%s (it now names another owner)"
                            % RETIRE_REFUSED_UNATTRIBUTED))
            continue
        if not gone:
            refused.append((path, "%s (its scope directory is present again, or its"
                            " absence cannot be observed)" % RETIRE_REFUSED_UNREADABLE))
            continue
        unbound = _unbound_reason(observed)
        if unbound is not None:
            refused.append((path, "%s (its evidence cannot be bound: %s)"
                            % (RETIRE_REFUSED_UNREADABLE, unbound)))
            continue
        if observed != before:
            refused.append((path, "%s (its evidence changed while it was read)"
                            % RETIRE_REFUSED_UNREADABLE))
            continue

        def remove_dangling(path=path, scope=scope, observed=observed):
            if _credential_evidence(path, scope, base) != observed:   # bound, as above
                return ("%s (its evidence changed before the removal)"
                        % RETIRE_REFUSED_UNREADABLE)
            kept = _remove_credential(path)
            if kept is not None:
                return "%s (%s; its directory is already gone)" % (
                    RETIRE_REFUSED_CREDENTIAL_KEPT, kept)
            return None
        stopped, outcome = admitted(remove_dangling)
        if stopped is not None:
            refused.append((path, stopped))
            continue
        if outcome is not None:
            refused.append((path, outcome))
        else:
            retired.append(path)
    return retired, refused


#: Task 8 R25-1: the held section's evidence reader is BOUNDED. A record
#: larger than one read block is not read: it cannot be bound, and the object
#: is refused. Every record this module writes (credential, binding key,
#: nonce, leader start, group) is far smaller. Likewise, an owned-root prefix
#: with more entries than this is not enumerated inside the section.
EVIDENCE_RECORD_BYTES = READ_BLOCK_BYTES
EVIDENCE_MAX_ENTRIES = 1024

#: An observation the evidence reader could not BIND to the object it
#: examined. The reader places one in exactly two shapes: bare, or as an
#: item's LAST field. ``_unbound_reason`` finds both, and the retirement
#: refuses the object before any comparison.
_Unbound = collections.namedtuple("_Unbound", ("reason",))

#: The IDENTITY readers' opens (``_open_examined``: every directory, and every
#: record ``lstat`` showed regular). They never follow a final link (``lstat``
#: did not), and a FIFO cannot stall them. The rule is
#: ``workflow_authority.atomic``'s observer read: what is OPENED is
#: re-examined through its descriptor, never trusted from a path checked
#: beforehand. (A record that IS a link is opened differently, on purpose:
#: ``_linked_record_digest``.)
_EVIDENCE_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW


def _close_evidence(descriptor):
    """Close ONE descriptor the evidence reader opened: None, or the
    failure's class (an unbound observation, never raised)."""
    try:
        os.close(descriptor)
    except OSError as exc:
        return exc.__class__.__name__
    return None


def _stat_entry(where, name):
    """``lstat`` of ONE entry: ``(info, None)``, or ``(None, observation)``.

    ``where`` is an OPENED directory's descriptor, ``name`` relative to it;
    or None, ``name`` a path. Missing relative to an opened directory is
    genuine absence in THAT directory. Missing by path is absence only when
    the traversal (``classify_missing``) establishes it.

    Every OTHER outcome is an observation NOT made, and it is ``_Unbound``.
    That covers an ``lstat`` that failed, and a missing path the traversal
    cannot call absent. So it refuses the object (``_unbound_reason``). It
    is never an ordinary value, on which two failed snapshots would compare
    equal (the Lead's R25 §4-quater)."""
    try:
        return os.stat(name, dir_fd=where, follow_symlinks=False), None
    except FileNotFoundError:
        if where is not None:
            return None, (READ_ABSENT, None)
        missing = classify_missing(name)
        if missing.availability == READ_ABSENT:
            return None, (READ_ABSENT, None)
        return None, (_Unbound("its observation is unavailable (%s)" % missing.problem),)
    except OSError as exc:
        return None, (_Unbound("its observation failed (%s)" % exc.__class__.__name__),)


def _identity_of(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _open_examined(where, name, info, flags=0):
    """Open the entry ``info`` describes WITHOUT following a link or waiting,
    and confirm through the descriptor that what was OPENED is that object:
    same device, inode and type. ``(descriptor, None)`` or ``(None,
    reason)``; a descriptor returned is the caller's to close."""
    try:
        descriptor = os.open(name, _EVIDENCE_OPEN_FLAGS | flags, dir_fd=where)
    except OSError as exc:
        return None, exc.__class__.__name__
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        _close_evidence(descriptor)
        return None, exc.__class__.__name__
    if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
            info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)):
        _close_evidence(descriptor)
        return None, "the object opened is not the one examined"
    return descriptor, None


def _bounded_digest(descriptor, info):
    """``(digest, problem)`` for the regular file OPEN at ``descriptor``,
    which ``info`` (its ``fstat``, or the ``lstat`` it was opened as)
    describes. Read in blocks and BOUNDED: never more than
    ``EVIDENCE_RECORD_BYTES`` + 1 bytes. A file that is larger, or whose
    size or modification time changed while it was read, has no digest."""
    digest, count = hashlib.sha256(), 0
    try:
        while count <= EVIDENCE_RECORD_BYTES:
            block = os.read(descriptor, min(READ_BLOCK_BYTES,
                                            EVIDENCE_RECORD_BYTES + 1 - count))
            if not block:
                break
            digest.update(block)
            count += len(block)
        after = os.fstat(descriptor)
    except OSError as exc:
        return None, exc.__class__.__name__
    if count > EVIDENCE_RECORD_BYTES:
        return None, "larger than %d bytes" % EVIDENCE_RECORD_BYTES
    if (count, after.st_size, after.st_mtime_ns) != (
            info.st_size, info.st_size, info.st_mtime_ns):
        return None, "it changed while it was read"
    return digest.hexdigest(), None


def _record_digest(where, name, info):
    """The digest of ONE regular record ``info`` describes, read from the
    descriptor of the object examined (``_open_examined``), BOUNDED
    (``_bounded_digest``). A record that is larger, that cannot be opened as
    the object examined, or that changed while it was read is
    ``_Unbound``."""
    if info.st_size > EVIDENCE_RECORD_BYTES:
        return _Unbound("larger than %d bytes" % EVIDENCE_RECORD_BYTES)
    descriptor, why = _open_examined(where, name, info)
    if descriptor is None:
        return _Unbound(why)
    try:
        digest, problem = _bounded_digest(descriptor, info)
    finally:
        closed = _close_evidence(descriptor)
    problem = problem or closed
    return _Unbound(problem) if problem is not None else digest


def _linked_record_digest(where, name, link):
    """ONE record that IS a symbolic link (``link``, its ``lstat``). Its
    reader follows it (``open``), so this reader follows it too, ONCE: an
    open WITHOUT ``O_NOFOLLOW`` (deliberately) and with ``O_NONBLOCK`` (a
    FIFO target cannot stall it), then everything from that descriptor.
    Returns ``(target identity, digest)``, which binds three things:
    - the link, re-observed unchanged after the read;
    - the target's identity, from ``fstat`` on what was opened;
    - the target's bounded digest (``_bounded_digest``).

    A target that is not a regular file, is larger than the bound, or
    changed while it was read, and a link that changed meanwhile or cannot
    be re-observed, are each ``_Unbound``. A VALID link is therefore bound: retirement goes through
    it, as recovery does (R24 restored such links). A change of the target
    it reaches is seen."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK, dir_fd=where)
    except OSError as exc:
        return _Unbound(exc.__class__.__name__)
    target, digest, problem = None, None, None
    try:
        target = os.fstat(descriptor)
        if not stat.S_ISREG(target.st_mode):
            problem = "its link's target is not a regular file"
        elif target.st_size > EVIDENCE_RECORD_BYTES:
            problem = "larger than %d bytes" % EVIDENCE_RECORD_BYTES
        else:
            digest, problem = _bounded_digest(descriptor, target)
    except OSError as exc:
        problem = exc.__class__.__name__
    finally:
        closed = _close_evidence(descriptor)
    problem = problem or closed
    if problem is None:
        again, _observed = _stat_entry(where, name)
        if again is None:
            problem = "its link cannot be re-observed"
        elif _identity_of(again) != _identity_of(link):
            problem = "its link changed while it was read"
    if problem is not None:
        return _Unbound(problem)
    return (_identity_of(target), digest)


def _entry_evidence(where, name, label, content):
    """``(evidence, info)`` for ONE entry: its identity (device, inode, mode,
    size, modification time) and, when ``content``, its bounded digest —
    or what was observed instead of it. ``info`` is the ``lstat`` result,
    or None.

    A record (``content``) is bound as its reader reads it:
    - a regular file through ``_record_digest``;
    - a symbolic link FOLLOWED, as ``open`` follows it
      (``_linked_record_digest``);
    - anything else (a FIFO, device, socket or directory) is ``_Unbound``."""
    info, missing = _stat_entry(where, name)
    if info is None:
        return (label,) + missing, None
    digest = None
    if content:
        if stat.S_ISREG(info.st_mode):
            digest = _record_digest(where, name, info)
        elif stat.S_ISLNK(info.st_mode):
            digest = _linked_record_digest(where, name, info)
        else:
            digest = _Unbound("not a regular file")
    return (label,) + _identity_of(info) + (digest,), info


def _directory_entries(descriptor):
    """The names in an OPENED directory, sorted, or an ``_Unbound``: more
    than ``EVIDENCE_MAX_ENTRIES`` are never collected."""
    names = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if len(names) == EVIDENCE_MAX_ENTRIES:
                    return _Unbound("more than %d entries" % EVIDENCE_MAX_ENTRIES)
                names.append(entry.name)
    except OSError as exc:
        return _Unbound(exc.__class__.__name__)
    return sorted(names)


def _tree_evidence(where, name, label, depth, seen):
    """Append the evidence of the entry ``name`` and, while it is a
    directory opened as the object examined, what lies under it:
    - ``depth`` 2 (a scope): its owned-root prefix;
    - ``depth`` 1 (the prefix): each entry in it;
    - ``depth`` 0 (an owned root): its nonce, leader-start and group
      records, each with its bounded digest.
    Every lookup below is RELATIVE to the parent's descriptor, so it
    describes the object the parent's evidence identified.

    The scope entry and its prefix, when present, must BE directories: the
    ownership readers reach the records THROUGH them (``lstat`` and ``open``
    follow an ancestor link), while this reader does not. Anything else is
    ``_Unbound``. An owned-root prefix entry that is a regular file is not a
    root (the readers skip it), so it carries its identity alone."""
    item, info = _entry_evidence(where, name, label, content=False)
    seen.append(item)
    if info is not None and depth > 0 and not stat.S_ISDIR(info.st_mode):
        seen.append(_Unbound("%s: not a directory" % label))
        return
    if info is None or not stat.S_ISDIR(info.st_mode):
        return
    descriptor, why = _open_examined(where, name, info, os.O_DIRECTORY)
    if descriptor is None:
        seen.append(_Unbound(why))
        return
    try:
        if depth == 2:
            _tree_evidence(descriptor, OWNED_ROOT_DIR_NAME,
                           os.path.join(label, OWNED_ROOT_DIR_NAME), 1, seen)
            # Task 8 R27-1: the scope's OWNER LEDGER, which the readers now
            # consult for a contradiction (``ledger_groups``) — bound by its
            # IDENTITY (an append changes its size, a rewrite its modification
            # time), never its content: it grows without bound. Genuinely
            # absent, it binds as absent. Present but neither a regular file
            # nor a link to follow (a FIFO, a device, a directory), it is
            # ``_Unbound``: the first evidence read GATES the readers (R26-1).
            ledger_label = os.path.join(label, LEDGER_FILE_NAME)
            item, ledger = _entry_evidence(descriptor, LEDGER_FILE_NAME, ledger_label, False)
            seen.append(item)
            if ledger is not None and not (stat.S_ISREG(ledger.st_mode)
                                           or stat.S_ISLNK(ledger.st_mode)):
                seen.append(_Unbound("%s: not a regular file" % ledger_label))
        elif depth == 1:
            names = _directory_entries(descriptor)
            if isinstance(names, _Unbound):
                seen.append(names)
            else:
                for child in names:
                    _tree_evidence(descriptor, child, os.path.join(label, child), 0, seen)
        else:
            for record in (OWNED_ROOT_NONCE_FILE, OWNED_ROOT_START_FILE,
                           OWNED_ROOT_PGID_FILE):
                seen.append(_entry_evidence(descriptor, record,
                                            os.path.join(label, record), True)[0])
    finally:
        closed = _close_evidence(descriptor)
    if closed is not None:
        seen.append(_Unbound(closed))


def _unbound_reason(evidence):
    """Why ``evidence`` could not be bound to the objects it examined, or
    None.

    Every observation the reader could NOT make is an ``_Unbound``, bare or
    as an item's last field. That covers:
    - a failed ``lstat``, or a missing path the traversal cannot call
      absent (``_stat_entry``);
    - a record that could not be opened as the object examined, or read
      within its bound;
    - a listing past its bound;
    - a record that is not a regular file or a link to one, and a scope
      entry or prefix that is not a directory;
    - a failed close.
    So this finds them all.

    ``retire_workflow_scopes`` refuses such evidence BEFORE any admission.
    Only bound evidence is compared, so two failed observations can never
    match each other."""
    for item in evidence:
        if isinstance(item, _Unbound):
            return item.reason
        if isinstance(item, tuple) and item and isinstance(item[-1], _Unbound):
            return "%s: %s" % (item[0], item[-1].reason)
    return None


def _ownership_evidence(directory, credential, base=None):
    """Task 8 R25-1: the ownership evidence ONE scope's retirement rests on,
    as a comparable value. It covers:
    - its credential and the store's binding key (attribution), each
      with its bounded digest;
    - the scope entry, its owned-root prefix, each entry there, and each
      owned root's records (``retirement_refusal``);
    - (Task 8 R27-1) the scope's owner ledger, by identity alone
      (``retirement_refusal`` reads it for a contradiction).

    THE READER IS LOCAL, NON-WAITING AND BOUNDED. It has TWO kinds of open,
    and every open of either kind is ``O_NONBLOCK``:
    - THE IDENTITY READERS (``_open_examined``: the scope, prefix and roots,
      and every record ``lstat`` showed regular) open with
      ``_EVIDENCE_OPEN_FLAGS`` (``O_RDONLY | O_NONBLOCK | O_NOFOLLOW``).
      They never follow a link ``lstat`` did not. What was opened is
      re-checked through ``fstat`` on its descriptor (same device, inode,
      type) before anything is read from it — ``workflow_authority
      .atomic``'s observer-read rule.
    - A RECORD THAT IS A LINK (``_linked_record_digest``) is FOLLOWED,
      deliberately and exactly once, as its reader follows it: one open
      with ``O_RDONLY | O_NONBLOCK`` (no ``O_NOFOLLOW``), the OPENED target
      ``fstat``-ed (it must be a regular file), its digest bounded, and the
      link re-observed unchanged afterwards.
    The scope, prefix and roots are walked RELATIVE to their parents'
    opened descriptors. Each record is read in ``READ_BLOCK_BYTES`` blocks,
    never more than ``EVIDENCE_RECORD_BYTES`` + 1 bytes, and listings stop
    past ``EVIDENCE_MAX_ENTRIES``. No subprocess, no other scope, no process
    query, no lock. "NON-WAITING" means no open waits on another party (a
    FIFO's writer). It does not bound the latency of the filesystem's own
    calls: a failing or remote filesystem can still stall one (a limit,
    stated). Anything the reader cannot bind is ``_Unbound``
    (``_unbound_reason``), which refuses the object:
    - every observation not made (``_stat_entry``);
    - a scope entry or prefix that is not a directory — the readers reach
      the records THROUGH them, and enumeration already refuses such an
      entry;
    - a record, credential or key that is neither a regular file nor a link
      to one.
    A record that IS a link is followed ONCE, as its reader follows it
    (``_linked_record_digest``), so this value covers what the reader read.
    A VALID link is retired through, as R24 restored it.

    WHY unchanged evidence keeps the readers' verdict (an argument from
    the source, not an observation). This value covers every FILE the
    readers consult. The one input it cannot cover is PROCESS state — a
    recorded group's liveness, and its leader's start time — and that
    cannot turn a retirable verdict unsafe:
    - a group observed gone cannot come back as THIS workflow's (a reused
      id fails corroboration);
    - a NEW group needs a new owned root first (``create_owned_root``
      precedes the spawn), which changes this value.

    The LIMIT (stated, not hidden): the identity fields and the digest of
    one record are not one atomic observation. A read is bound to the
    object ``lstat`` identified, and is refused if its size or
    modification time moved while it was read. An in-place rewrite that
    keeps both (within the filesystem's timestamp granularity) between two
    snapshots is not seen by this value. Likewise, the snapshot and the
    path-based effects after it (``unlink``, ``rmtree``) are not one atomic
    step. No wait separates them, but a concurrent writer is not excluded
    by any lock this module holds."""
    seen = [_entry_evidence(None, credential, credential, True)[0],
            _entry_evidence(None, os.path.join(assignment_base(base), ASSIGNMENT_KEY_FILE),
                            ASSIGNMENT_KEY_FILE, True)[0]]
    _tree_evidence(None, directory, directory, 2, seen)
    return tuple(seen)


def _credential_evidence(path, scope, base=None):
    """Task 8 R25-1: the evidence a dangling credential's removal rests on,
    by ``_ownership_evidence``'s reader: the credential and the binding
    key, each with its bounded digest, and its scope directory's entry
    (genuinely absent, or what stands there instead)."""
    return (_entry_evidence(None, path, path, True)[0],
            _entry_evidence(None, os.path.join(assignment_base(base), ASSIGNMENT_KEY_FILE),
                            ASSIGNMENT_KEY_FILE, True)[0],
            _entry_evidence(None, scope, scope, False)[0])


#: ``_absence_unobserved``: the entry was examined, and it is there.
STILL_PRESENT = "still present"


def _absence_unobserved(path):
    """None when ``path`` is OBSERVED genuinely absent; otherwise why not:
    ``STILL_PRESENT``, or that its absence cannot be observed (naming why).

    Task 8 R25-2: ``lstat`` raises FileNotFoundError THROUGH a dangling
    ANCESTOR too, so that error alone never reads as absent. Only the
    traversal (``classify_missing``) establishing GENUINE absence does."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        missing = classify_missing(path)
        if missing.availability == READ_ABSENT:
            return None
        return "its absence cannot be observed (%s)" % missing.problem
    except OSError as exc:
        return "its absence cannot be observed (%s)" % exc.__class__.__name__
    return STILL_PRESENT


def _present(path):
    """Whether ``path`` may still exist: False ONLY when its genuine absence
    is OBSERVED (``_absence_unobserved``) — an entry that cannot be
    examined is not absent."""
    return _absence_unobserved(path) is not None


def _remove_credential(path):
    """Remove one assignment credential and OBSERVE it gone: None, or why it
    is kept (the error's class, or that it is still present).

    Task 8 R25-2: an ``unlink`` raising FileNotFoundError, and the absence
    observed after it, count as GONE only when the traversal
    (``classify_missing``) establishes genuine absence. Through a dangling
    ANCESTOR both raise FileNotFoundError while the credential — the sole
    deletion proof — may still exist. That is "its absence cannot be
    observed": the obligation is KEPT and reported, never "removed"."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass                           # gone already — only if observed below
    except OSError as exc:
        return exc.__class__.__name__
    return _absence_unobserved(path)   # None only when OBSERVED gone


def _dangling_credentials(control_identity, workflow_id, base=None):
    """Task 8 R20-B (Addendum C): ``(found, unread, problem)`` — this
    workflow's assignment credentials whose scope directory is ALREADY
    ABSENT (a removal whose credential half failed earlier).

    ``found`` (``(name, path)``) are selected by the RECORD each holds
    (``read_assignment``: its integrity binding verified, naming exactly
    this workflow and control repository), never by a file name alone. Of
    the rest (C-2b), only PROVEN cases are skipped: a credential that is
    genuinely gone (no assignment, and ``lstat`` raising FileNotFoundError),
    or one that reads and names another owner. One whose NAME claims this
    workflow but that cannot be read or does not verify (MALFORMED,
    FORGED) is AMBIGUOUS, never absent: ``unread`` (``(name, path,
    reason)``), which the retirement refuses — and never removes.
    ``problem`` is ``RETIRE_REFUSED_UNREADABLE`` when the store cannot be
    listed."""
    store = assignment_base(base)
    try:
        names = sorted(os.listdir(store))
    except FileNotFoundError:
        # Task 8 R24-2: no store only when the traversal says it is GENUINELY
        # missing; a dangling store link is a store that cannot be listed.
        if classify_missing(store).availability == READ_ABSENT:
            return [], [], None
        return [], [], RETIRE_REFUSED_UNREADABLE
    except OSError:
        return [], [], RETIRE_REFUSED_UNREADABLE
    digest = control_digest(control_identity)
    prefix = owned_root_base(base)
    found, unread = [], []
    for entry in names:
        if not entry.endswith(ASSIGNMENT_SUFFIX):
            continue
        name = entry[:-len(ASSIGNMENT_SUFFIX)]
        claimed = parse_scope(os.path.join(prefix, name))
        if (claimed is None or claimed.owner_type != OWNER_TYPE_WORKFLOW
                or claimed.control_digest != digest or claimed.owner_id != workflow_id):
            continue
        if _present(os.path.join(prefix, name)):
            continue                   # its directory is retirement's to decide
        path = os.path.join(store, entry)
        record, reason = read_assignment(name, base=base)
        if record is None:
            if reason == UNATTRIBUTED_NO_ASSIGNMENT and not _present(path):
                continue               # genuinely gone
            unread.append((name, path, "%s (%s)" % (RETIRE_REFUSED_CREDENTIAL_UNREAD, reason)))
            continue
        if (record.get("owner_type"), record.get("owner_id"),
                record.get("control_identity")) != (
                    OWNER_TYPE_WORKFLOW, workflow_id, control_identity):
            continue                   # it reads, and names another owner
        found.append((name, path))
    return found, unread, None


def recover_attributed(base=None, settle_seconds=None,
                       current_owners=None):
    """Recover every ASSIGNED scope, reporting per owner.

    Returns ``(results, unattributed)`` where ``results`` is a list of
    ``(ScopeIdentity, recovered, stuck, unstamped, uncorroborated)``
    and ``unattributed`` is ``(directory, reason)`` pairs. The identity
    travels as ONE value rather than as spread fields, so that within
    a row it cannot be unpacked into the wrong arity when the identity
    gains a part — which is exactly what the control digest just did.

    Every record acted on carries an owner READ FROM THE PROTECTED
    STORE before the action (AG-3). The name is a label and is used as
    one: it says which assignment to look for, and within this gate
    decides nothing.

    Task 8 R21-2: returns a ``RecoveryReport`` — it unpacks as
    ``(results, unattributed)`` exactly as before, and carries
    ``unavailable``: every observation that could not be made (an
    unreadable scope base or entry, owned-root prefix, root or group
    record), REPORTED as unavailable, never read as absent. Nothing it
    covers is acted on; once the source reads again, recovery acts on the
    real records — the assignment and the corroboration, never a guess.

    Task 8 R22-1/R22-2: and so are a live group whose leader corroboration
    could not be obtained (``uncorroborated``, never signalled), a
    credential that could not be read, and — ``current_owners`` passed as
    ``UnavailableOwners`` — the durable workflow record itself (counted
    once here, and each workflow-owned scope left UNATTRIBUTED as owners
    unavailable, never STALE).
    """
    unavailable = []
    if isinstance(current_owners, UnavailableOwners):
        unavailable.append((current_owners.source, current_owners.reason))
    attributed, unattributed = classify_scopes(
        base, current_owners=current_owners, unavailable=unavailable
    )
    results = []
    for identity, directory in attributed:
        recovered, stuck, unstamped, uncorroborated = recover_orphans(
            directory, settle_seconds=settle_seconds, unavailable=unavailable
        )
        if recovered or stuck or unstamped or uncorroborated:
            results.append((
                identity, recovered, stuck, unstamped, uncorroborated
            ))
    return RecoveryReport(results, unattributed, unavailable)


class RecoveryReport(tuple):
    """Task 8 R21-2: ``(results, unattributed)`` — unpacking exactly as the
    2-tuple ``recover_attributed`` always returned — plus ``unavailable``,
    ``(path, reason)`` for every observation that could not be made."""

    def __new__(cls, results, unattributed, unavailable):
        report = super(RecoveryReport, cls).__new__(cls, (results, unattributed))
        report.unavailable = list(unavailable)
        return report
