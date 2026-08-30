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
import sys
import signal
import time

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
    not an error here: it means this process has no child to collect here."""
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


def pending_nonces(directory=None):
    """Nonces recorded as PENDING for which no group id was later recorded
    in this ledger.

    A non-empty result means a spawn crashed inside the window between
    the pending record and the group record. It is REPORTED rather
    than swept, because what that case needs is evidence, not a
    broader kill.
    """
    import json
    path = ledger_path(directory)
    if path is None or not os.path.exists(path):
        return []
    pending, resolved = [], set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
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
    """Every process group id the ledger names, as a set of ints."""
    import json
    path = ledger_path(directory)
    if path is None or not os.path.exists(path):
        return set()
    found = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            pgid = row.get("pgid")
            if isinstance(pgid, int) and not isinstance(pgid, bool):
                found.add(pgid)
    return found


def spawn_owned(argv, label, directory=None,
                owned_root_base_dir=None, **popen_kwargs):
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
    # import. `spawn_stamp` deliberately imports only `os` and `sys`
    # so it can run as a plain script.
    argv = [
        sys.executable, _STAMP_WRAPPER, root, "--",
    ] + list(argv)
    popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)
    # The parent's records are now CONFIRMATION rather than the only
    # evidence: the child stamped its own root before exec, so a
    # parent that dies on the next line still leaves a stamped root.
    record_owned_group(proc.pid, label, directory, nonce=nonce)
    record_owned_root_group(root, proc.pid)
    return proc


def reap_owned(pgid, directory=None, settle_seconds=None,
               sleeper=None, clock=None):
    """Reap a process group THIS component recorded as its own.

    Within this function, a group the ledger does not name is refused,
    and so are group 0, group 1, and this process's own group. Unlike `reap_group` it does
    NOT require the leader to still be alive, because the ledger — not
    the leader — is the ownership evidence, and an orphaned group with
    a dead leader is precisely the case that must remain reapable.
    """
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 1:
        return REFUSED_NOT_IN_LEDGER, (
            "%r is not a usable process group id" % (pgid,)
        )
    if pgid == os.getpgrp():
        return REFUSED_NOT_IN_LEDGER, (
            "refusing to reap this process's OWN group"
        )
    if pgid not in owned_groups(directory):
        return REFUSED_NOT_IN_LEDGER, (
            "group %d is not recorded in this component's owner"
            " ledger; ownership is recorded evidence, never a name,"
            " a command pattern or a start time" % pgid
        )
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    settle = (
        REAP_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            _reap_leader(pgid)
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
    return reaped, stuck, pending_nonces(directory)


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


def owned_root_record(directory):
    """Everything an owned root durably claims: nonce, pgid, start.

    A record, not a verdict. `group_is_ours` is what turns it into
    one.
    """
    record = {"nonce": None, "pgid": None, "leader_start": None}
    for key, name in (
        ("nonce", OWNED_ROOT_NONCE_FILE),
        ("leader_start", OWNED_ROOT_START_FILE),
        ("pgid", OWNED_ROOT_PGID_FILE),
    ):
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            continue
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

    Anything else — no nonce, no recorded start, a start that differs,
    a group that is gone — returns a reason and no pgid. Fail-closed
    in the direction that matters: within recovery an uncorroborated
    group is reported and left alone rather than signalled.

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
        return None, None                       # gone, nothing to do
    if live_start != recorded_start:
        return None, UNCORROBORATED_START_MISMATCH
    return pgid, None


def owned_roots(base=None):
    """``(directory, pgid_or_None)`` for every owned root on disk.

    A root whose pgid file is absent is reported with None rather than
    skipped: it is a spawn that crashed before its group could be
    stamped, and losing it silently is the failure this whole
    mechanism exists to prevent.
    """
    prefix = owned_root_base(base)
    if not os.path.isdir(prefix):
        return []
    found = []
    for name in sorted(os.listdir(prefix)):
        directory = os.path.join(prefix, name)
        if not os.path.isdir(directory):
            continue
        pgid = None
        path = os.path.join(directory, OWNED_ROOT_PGID_FILE)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    pgid = int(handle.read().strip())
            except (OSError, ValueError):
                pgid = None
        found.append((directory, pgid))
    return found


def recover_orphans(base=None, settle_seconds=None, sleeper=None,
                    clock=None):
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
    """
    recovered, stuck, unstamped, uncorroborated = [], [], [], []
    for directory, pgid in owned_roots(base):
        if pgid is None:
            unstamped.append(directory)
            continue
        if pgid <= 1 or pgid == os.getpgrp():
            continue
        if not _group_alive(pgid):
            continue
        # R-54 AR-3: ALIVE IS NOT OURS. Within this check a live
        # number says nothing about whether the process holding it is
        # the one this record names, because the OS reuses the
        # number. Corroborate before signalling.
        ours, reason = group_is_ours(directory)
        if ours is None:
            if reason is not None:
                uncorroborated.append((directory, pgid, reason))
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
    """
    sleeper = sleeper or time.sleep
    clock = clock or time.monotonic
    settle = (
        REAP_SETTLE_SECONDS if settle_seconds is None else settle_seconds
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            _reap_leader(pgid)
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
    with open(path, "rb") as handle:
        key = handle.read()
    if len(key) < 32:
        raise OSError(
            "the scope assignment binding key at %s is truncated;"
            " refusing to bind or verify against it" % path
        )
    return key


def _binding_for(record, base=None):
    payload = json.dumps(
        {field: record.get(field) for field in ASSIGNMENT_BOUND_FIELDS},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _binding_key(base), payload, hashlib.sha256
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
    """
    path = assignment_path(name, base=base)
    if not os.path.exists(path):
        return None, UNATTRIBUTED_NO_ASSIGNMENT
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None, UNATTRIBUTED_MALFORMED
    if not isinstance(record, dict):
        return None, UNATTRIBUTED_MALFORMED
    for field in ASSIGNMENT_BOUND_FIELDS:
        if record.get(field) is None:
            return None, UNATTRIBUTED_MALFORMED
    presented = record.get("binding")
    if not isinstance(presented, str):
        return None, UNATTRIBUTED_MALFORMED
    try:
        expected = _binding_for(record, base=base)
    except OSError:
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
    supply it.

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
        current_owners is not None
        and assigned.owner_type == OWNER_TYPE_WORKFLOW
        and tuple(assigned) not in {
            tuple(owner) for owner in current_owners
        }
    ):
        return None, UNATTRIBUTED_STALE
    return assigned, None


# --- Enumeration and recovery ---------------------------------------


def _scope_directories(base=None):
    prefix = owned_root_base(base)
    if not os.path.isdir(prefix):
        return []
    return [
        os.path.join(prefix, name)
        for name in sorted(os.listdir(prefix))
        if os.path.isdir(os.path.join(prefix, name))
    ]


def classify_scopes(base=None, current_owners=None):
    """``(attributed, unattributed)`` for every directory under the
    base — ONE walk, so that within this result the two lists cannot
    disagree.

    ``attributed`` is ``(identity, directory)``; ``unattributed`` is
    ``(directory, reason)``. Every directory lands in exactly one.
    """
    attributed, unattributed = [], []
    for directory in _scope_directories(base):
        identity, reason = validate_assignment(
            directory, base=base, current_owners=current_owners
        )
        if identity is None:
            unattributed.append((directory, reason))
        else:
            attributed.append((identity, directory))
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
    """
    for root, pgid in owned_roots(directory):
        if pgid is None or pgid <= 1 or not _group_alive(pgid):
            continue
        # AR-3: a live number is not a live process of ours.
        ours, _reason = group_is_ours(root)
        if ours is not None:
            return True
    return False


#: Why a scope was NOT retired. Reported, and the scope is left.
RETIRE_REFUSED_LIVE_GROUP = (
    "a corroborated group recorded in this scope is still running"
)
RETIRE_REFUSED_UNATTRIBUTED = (
    "the scope carries no valid assignment naming this workflow"
)


def workflow_scopes(control_identity, workflow_id, base=None):
    """Every scope whose ASSIGNMENT names exactly this owner.

    The assignment decides, within this selection, and the name does
    not (AG-2): a directory whose basename happens to parse into this
    workflow id is not this workflow's, and retirement is a removal,
    so the credential is what selects.
    """
    digest = control_digest(control_identity)
    found = []
    for directory in _scope_directories(base):
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


def retire_workflow_scopes(control_identity, workflow_id, base=None):
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

    REFUSES while a CORROBORATED group recorded in the scope is still
    running (AR-3): the record is the only evidence a later run could
    recover that process from, and removing it while the process lives
    is the leak this module exists to prevent. Refused scopes are
    REPORTED and left, so the next terminal cleanup retries — the same
    retain-and-retry shape AC-3 uses.

    Returns ``(retired, refused)`` where ``refused`` carries
    ``(directory, reason)``.
    """
    import shutil
    retired, refused = [], []
    for _identity, directory in workflow_scopes(
        control_identity, workflow_id, base=base
    ):
        if scope_has_live_group(directory):
            refused.append((directory, RETIRE_REFUSED_LIVE_GROUP))
            continue
        name = os.path.basename(directory.rstrip(os.sep))
        # The ASSIGNMENT goes first. It is the credential, and a scope
        # directory left behind without one is reported as
        # UNATTRIBUTED and left alone — which is the safe residue. The
        # reverse order would leave a credential pointing at a
        # directory that no longer exists, and a later run would have
        # to decide what that means.
        try:
            os.unlink(assignment_path(name, base=base))
        except OSError:
            pass
        shutil.rmtree(directory, ignore_errors=True)
        retired.append(directory)
    return retired, refused


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
    """
    attributed, unattributed = classify_scopes(
        base, current_owners=current_owners
    )
    results = []
    for identity, directory in attributed:
        recovered, stuck, unstamped, uncorroborated = recover_orphans(
            directory, settle_seconds=settle_seconds
        )
        if recovered or stuck or unstamped or uncorroborated:
            results.append((
                identity, recovered, stuck, unstamped, uncorroborated
            ))
    return results, unattributed
