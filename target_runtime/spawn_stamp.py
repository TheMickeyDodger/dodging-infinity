"""The child-side stamping wrapper (R-28 T-3).

Why this exists rather than `preexec_fn`
========================================

S-2 asked for CHILD-SIDE self-stamping so that a parent dying after
`Popen` returns still leaves a stamped root — the entity that survives
is the one that recorded itself. The first implementation did that with
`preexec_fn`, an arbitrary callable running in the child after fork and
before exec.

R-28 found the hazard: this repository runs threads, and CPython
documents `preexec_fn` as unsafe in threaded applications — a child can
deadlock between fork and exec if it touches a lock another thread held
at fork time. Writing a file takes locks. So that was a live deadlock
hazard rather than a residual, and it is removed.

This module is the replacement. The parent spawns

    python -m target_runtime.spawn_stamp <root> -- <argv...>

and this module, running as the child AFTER exec, writes its own
process-group id into ``<root>`` and then ``execv``s the real argv in
place. Stamping therefore happens in a fully exec'd process with no
inherited lock state, which is what makes it thread-safe; and because
``execv`` REPLACES this process rather than forking again, the pid and
group that were stamped are the ones the real program runs under.

What is still true of the child, stated with it: a program that changes its own process group after exec moves outside
the group this stamp names, and where the root is unwritable no stamp is
written — in which case this exits non-zero rather than exec'ing an
unattributable process.
"""

import os
import subprocess
import sys

PGID_FILE = "pgid"
#: The leader's START TIME, recorded beside its group id.
#:
#: R-54 AR-3: A RECORDED PGID IS NOT A DURABLE IDENTITY. The OS reuses
#: process-group numbers, so a record saying "group 44603 is ours"
#: becomes a record pointing at somebody else's process the moment the
#: original dies and the number comes round again — and the specimen
#: that produced this rule was exactly that: pgid 44603 recorded here,
#: later held by a system crash reporter, and empty by the time it was
#: re-checked.
#:
#: A start time distinguishes them. Two processes may share a number;
#: the pair (number, start time) survives reuse, because the reusing
#: process started later. Recorded by the CHILD, for the same reason
#: the group id is: the entity that survives a parent crash is the one
#: that recorded itself.
START_FILE = "leader-start"
EXIT_UNSTAMPABLE = 71


def leader_start_time(pid):
    """The kernel's start time for ``pid``, as an exact string.

    ``ps -o lstart=`` is used because it is the one field available on
    both platforms this runs on that names WHEN the process began.
    Within this module the string is compared for EQUALITY, and
    parsing it is not attempted: its format is the platform's
    business, and parsing would add a way to be wrong.

    Returns None for a process that is gone, and when the query
    itself fails. A None is NOT evidence: callers treat an
    uncorroborated group as not ours, which is the fail-closed
    direction.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except Exception:                             # noqa: BLE001
        # A failure to ask the OS yields None, and the breadth is
        # deliberate: this runs on a spawn path and inside recovery,
        # and an exception escaping here would turn "we could not
        # corroborate" into "the spawn failed". None is already the
        # fail-closed answer — within recovery an uncorroborated group
        # is reported and left alone — so a raise would add only a
        # new way for cleanup to abort.
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.decode("utf-8", "replace").strip()
    return text or None


def stamp(root, pid=None):
    """Write this process's group id AND its start time into ``root``.

    ``os.getpgrp()`` rather than ``os.getpid()``: the parent creates
    the child with ``start_new_session=True``, so the two coincide —
    but the group is what a reap acts on, so the group is what is
    recorded.

    The START TIME is written FIRST and the group id LAST, so a reader
    that finds a pgid can expect the corroboration beside it. The
    reverse order would leave a window in which a group id is
    readable and uncorroborated, and a reader in that window would
    have to choose between refusing a live record and trusting an
    uncorroborated one.
    """
    group = os.getpgrp() if pid is None else pid
    started = leader_start_time(group)
    if started is not None:
        start_path = os.path.join(root, START_FILE)
        with open(start_path, "w", encoding="utf-8") as handle:
            handle.write(started)
            handle.flush()
            os.fsync(handle.fileno())
    path = os.path.join(root, PGID_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(group))
        handle.flush()
        os.fsync(handle.fileno())
    return path


def main(argv):
    if len(argv) < 3 or "--" not in argv:
        sys.stderr.write(
            "usage: spawn_stamp <owned-root> -- <argv...>\n"
        )
        return 2
    root = argv[1]
    rest = argv[argv.index("--") + 1:]
    if not rest:
        sys.stderr.write("spawn_stamp: no command to exec\n")
        return 2
    try:
        stamp(root)
    except OSError as exc:
        sys.stderr.write(
            "spawn_stamp: could not stamp %s (%s); refusing to exec an"
            " unattributable process\n" % (root, exc)
        )
        return EXIT_UNSTAMPABLE
    os.execv(rest[0], rest)
    return 1                                    # pragma: no cover


if __name__ == "__main__":
    sys.exit(main(sys.argv))
