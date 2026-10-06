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
process-group id into ``<root>`` and then ``execvp``s the real argv in
place. Stamping therefore happens in a fully exec'd process with no
inherited lock state, which is what makes it thread-safe; and because
``execvp`` REPLACES this process rather than forking again, the pid and
group that were stamped are the ones the real program runs under.
The ``p`` variant preserves the PATH lookup that a direct
``subprocess.Popen(argv)`` performed before this wrapper existed.

What is still true of the child, stated with it: a program that changes its own process group after exec moves outside
the group this stamp names, and where the root is unwritable no stamp is
written — in which case this exits non-zero rather than exec'ing an
unattributable process. Task 8 R26: so is a record path that names
something this writer must not write (``_write_record``) — a FIFO, a
symbolic link (where the platform provides ``O_NOFOLLOW``), a hard link, a
directory: the child refuses to exec, and nothing is written there. Task 8
R27-1: each record is replaced ATOMICALLY, so any later failure — before its
publication, or after it (``PublicationUnproven``) — also refuses the exec and
leaves that record complete; what it leaves is stated at ``_write_record``.
"""

import errno
import os
import stat
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

#: Task 8 R27-1: how what ALREADY stands at a stamp record's path is EXAMINED
#: before any replacement is made — R26's open, WITHOUT ``O_CREAT``: for
#: writing, with ``O_NONBLOCK`` (an open of a FIFO with no reader fails at once,
#: ``ENXIO``, instead of waiting; this bounds nothing else) and, WHERE THE
#: PLATFORM PROVIDES IT, ``O_NOFOLLOW`` (a symbolic link refuses, ``ELOOP``).
#: Nothing is ever written, truncated or created through it. Where
#: ``os.O_NOFOLLOW`` is absent the fallback is 0, and the link is caught by the
#: last examination (``_examine_name``) instead.
_EXAMINE_FLAGS = os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
#: Task 8 R27-1: how a record's REPLACEMENT is created — a NEW name in the
#: record's own directory (so the rename stays on one filesystem), EXCLUSIVELY
#: (``O_EXCL``: never an object that already exists, never through a link),
#: ``O_NONBLOCK``, and ``O_NOFOLLOW`` where provided. Every byte this writer
#: writes goes into the object it creates here, and nowhere else.
_REPLACEMENT_FLAGS = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NONBLOCK
                      | getattr(os, "O_NOFOLLOW", 0))
#: Task 8 R27-1: the prefix of a replacement's name (``.stamp-replacement-
#: <record>-<16 hex>``) — never a record's name. Every reader opens a record by
#: its own fixed name, so no reader ever opens a replacement, published or not.
REPLACEMENT_PREFIX = ".stamp-replacement-"


class UnsupportedRecord(OSError):
    """Task 8 R26: a stamp record path that opened as something this writer
    must not write — not a regular file (a FIFO that has a reader, a device),
    or a regular file that has another name (a hard link). Raised before
    any byte is written or truncated. An ``OSError``, so the child refuses
    to exec (``main``) exactly as for an unwritable root. (Task 8 R27-1: also
    raised by the LAST examination, before the rename, and then for a
    symbolic link too; nothing is ever written through what it names.)"""


class PublicationUnproven(OSError):
    """Task 8 R27-1: the replacement WAS renamed into place — the record IS
    PUBLISHED, complete — and what followed could not be proven: the record's
    name was not observed to refer to the very single-named object this writer
    wrote, or the directory's ``fsync`` (the rename's durability) failed.
    Raised AFTER the publication and NEVER undone: no rollback deletes, renames
    or rewrites a published record (an undo would destroy a valid published
    identity). An ``OSError``, so the child refuses to exec (``main``) and the
    parent reports the spawn UNRESOLVED (``SpawnUnconfirmed``) — truthful
    unresolved, never a clean report."""


def _close_quietly(descriptor):
    """Close ``descriptor``; a failure is dropped, because it runs only while
    another failure is in flight, and that one is the one raised."""
    try:
        os.close(descriptor)
    except OSError:
        pass


def _single_regular(info, path):
    """Refuse (``UnsupportedRecord``) anything but a regular file with exactly
    ONE name."""
    if not stat.S_ISREG(info.st_mode):
        raise UnsupportedRecord(errno.EINVAL, "not a regular file", path)
    if info.st_nlink != 1:
        raise UnsupportedRecord(errno.EMLINK, "a regular file with %d names"
                                % info.st_nlink, path)


def _examine(path):
    """Task 8 R27-1: the FIRST examination of what stands at ``path``, BEFORE
    anything is created — R26's refusals, errno for errno: a symbolic link
    (``ELOOP``), a FIFO with no reader (``ENXIO``, at once), a directory
    (``EISDIR``), and, judged on the OPENED descriptor, a FIFO with a reader or
    a hard link (``UnsupportedRecord``). An absent record is no refusal: the
    replacement creates it (and an absent ROOT refuses the replacement's own
    creation, ``ENOENT``, with nothing created). This proves what stood there
    WHEN EXAMINED, and nothing about any later instant."""
    try:
        descriptor = os.open(path, _EXAMINE_FLAGS)
    except FileNotFoundError:
        return
    try:
        _single_regular(os.fstat(descriptor), path)
    except BaseException:
        _close_quietly(descriptor)
        raise
    os.close(descriptor)


def _examine_name(path):
    """Task 8 R27-1: the LAST examination before the rename, by ``lstat`` (no
    link followed, nothing opened): absent, or a regular file with exactly ONE
    name — otherwise ``UnsupportedRecord`` (a symbolic link: ``ELOOP``) and no
    rename. It NARROWS the window in which a substituted object could be
    replaced; it does NOT prove the state at the rename (see
    ``_write_record``)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise UnsupportedRecord(errno.ELOOP, "a symbolic link", path)
    _single_regular(info, path)


def _sync_directory(directory, path):
    """Task 8 R27-1: ``fsync`` the directory the record was renamed into, so
    the rename itself is durable. Runs AFTER the publication: a failure raises
    ``PublicationUnproven`` (the record IS published) and undoes nothing."""
    try:
        descriptor = os.open(directory or os.curdir,
                             os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise PublicationUnproven(exc.errno, "published; its directory could not be"
                                  " opened to make the rename durable (%s)"
                                  % (exc.strerror or exc), path) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        _close_quietly(descriptor)
        raise PublicationUnproven(exc.errno, "published; the rename's durability is"
                                  " UNPROVEN: the directory's fsync failed (%s)"
                                  % (exc.strerror or exc), path) from exc
    except BaseException:
        _close_quietly(descriptor)
        raise
    os.close(descriptor)


def _write_record(path, text):
    """Task 8 R27-1: replace ONE stamp record with ``text`` ATOMICALLY — the
    record is always EITHER its previous object OR ``text``, complete: never
    empty, never a fragment that reads as a different group or start time.
    R26's in-place writer truncated first, so a failed or interrupted
    replacement — the PARENT's confirmation after the child had stamped itself
    — destroyed the child's valid record; a short write could leave ``446`` of
    ``44603``, a valid-looking, unrelated, probably gone group.

    THE STEPS:
    1. EXAMINE what stands at ``path`` (``_examine``): R26's refusals, errno
       for errno, BEFORE anything is created.
    2. CREATE a replacement beside it (``_REPLACEMENT_FLAGS``: exclusive,
       never through a link, never waiting), check it is a single-named
       regular file, write ``text`` whole, ``fsync`` it. Its descriptor stays
       OPEN until the end.
    3. EXAMINE the name again (``_examine_name``), then RENAME the replacement
       over ``path`` — the publication.
    4. CONFIRM, through the still-open descriptor, that ``path`` now names THE
       VERY OBJECT written (same device and inode) and that it has exactly one
       name; then ``fsync`` the directory (``_sync_directory``).

    WHAT HOLDS AT THE REPLACEMENT BOUNDARY. ``rename(2)`` takes no identity
    predicate: it replaces whatever entry stands at ``path`` at that instant.
    So:
    - NOTHING is ever written to, truncated, opened for data or followed
      through ANY object at ``path`` — whenever it was put there. Every byte
      goes into the object this writer created exclusively. A FIFO, a link or
      an extra name planted at ANY moment never receives a byte, never makes
      this wait, and never becomes part of the published record.
    - An object present at an EXAMINATION is REFUSED (steps 1 and 3) and left
      exactly as it is. The examinations prove what stood there WHEN EXAMINED,
      not at the rename.
    - An entry substituted AFTER the last examination and BEFORE the rename is
      REPLACED by the rename: that name now refers to the published record. The
      substituted object is never opened or written; it survives under any
      other name it has. (A DIRECTORY there makes the rename fail: nothing is
      published.)
    - The writer reports SUCCESS only if, after the rename, ``path`` was
      observed naming THE VERY single-named object it wrote. Otherwise it
      raises ``PublicationUnproven``: the record WAS published, and is never
      rolled back.

    WHAT A FAILURE LEAVES. Every failure is an ``OSError``.
    - BEFORE the rename (an examination's refusal, the replacement's creation,
      write, ``fsync``, the last examination, the rename itself): ``path`` is
      UNTOUCHED — its previous object, complete, or still absent. A replacement
      created is DISCARDED (its own name unlinked — never ``path``).
    - AFTER the rename (the confirmation, the directory's ``fsync``):
      ``PublicationUnproven``. ``path`` holds ``text``, complete, unless
      something else replaced it since. Across a crash it is the previous
      object or ``text``, each complete. NEVER undone.
    - A writer KILLED mid-way cannot discard: ``path`` is still its previous
      object or ``text``, complete, and a replacement may remain beside it
      under ``REPLACEMENT_PREFIX`` — published by nobody, opened by no reader.
    If closing a descriptor fails while another failure is in flight, the
    in-flight failure is the one raised.

    CONCURRENCY. Two writers of one record (the child's stamp and the parent's
    confirmation) each publish a COMPLETE record; the later rename wins. A
    reader that opens a record by name gets the previous object or the new
    one, each complete. A reader that compares an ``lstat`` with what it then
    opened (``process_ownership._open_examined``) can see the record replaced
    in between, and refuses — unavailable, never settlement.

    "Never waits" means only that no open waits on a FIFO: this bounds nothing
    about a regular file's write, ``fsync`` or rename latency. It makes no
    claim about the directories above the record."""
    directory, name = os.path.split(path)
    _examine(path)
    replacement = os.path.join(directory, "%s%s-%s" % (
        REPLACEMENT_PREFIX, name, os.urandom(8).hex()))
    descriptor = os.open(replacement, _REPLACEMENT_FLAGS, 0o666)
    try:
        try:
            _single_regular(os.fstat(descriptor), replacement)
            data = memoryview(text.encode("utf-8"))
            while data:
                data = data[os.write(descriptor, data):]
            os.fsync(descriptor)
            _examine_name(path)
            os.rename(replacement, path)                 # THE PUBLICATION
        except BaseException:
            try:
                os.unlink(replacement)                   # its OWN name: never ``path``
            except OSError:
                pass                       # the in-flight failure is the one raised
            raise
        try:
            published, written = os.lstat(path), os.fstat(descriptor)
        except OSError as exc:
            raise PublicationUnproven(exc.errno, "published; the record could not be"
                                      " observed after the rename (%s)"
                                      % (exc.strerror or exc), path) from exc
        if (published.st_dev, published.st_ino) != (written.st_dev, written.st_ino):
            raise PublicationUnproven(errno.ESTALE, "published; the record no longer names"
                                      " the object written", path)
        if written.st_nlink != 1:
            raise PublicationUnproven(errno.EMLINK, "published; the object written has"
                                      " %d names" % written.st_nlink, path)
    except BaseException:
        _close_quietly(descriptor)
        raise
    os.close(descriptor)
    _sync_directory(directory, path)


#: Task 8 R28-1: the leader-start query, and its BOUND (seconds). The query runs
#: inside the reap's proof->signal span when the not-held proof corroborates a
#: group, so it is bounded: one that has not answered within the bound is None —
#: the same fail-closed answer as a gone process or a failed query.
LEADER_QUERY = ("ps", "-o", "lstart=", "-p")
LEADER_QUERY_SECONDS = 5.0


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

    Task 8 R28-1: BOUNDED by ``LEADER_QUERY_SECONDS``; a query that has not
    answered by then is None, like any failed query — never "the spawn
    failed" (the stamp then writes no start record, so the group is never
    corroborated). ``subprocess.run`` kills that query's OWN child on expiry
    and then waits for it without a bound: a query that a SIGKILL cannot end
    is outside this bound.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    try:
        completed = subprocess.run(
            list(LEADER_QUERY) + [str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=LEADER_QUERY_SECONDS,
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

    Task 8 R26: each record is written by ``_write_record`` — validated
    before any write; a FIFO open never waits for a reader (this bounds no
    regular-file write or ``fsync`` latency); never through a symbolic link
    where the platform provides ``O_NOFOLLOW`` — each ``fsync``-ed, START
    first. A START refused BEFORE its write therefore leaves no pgid written
    at all.

    Task 8 R27-1: each record is REPLACED ATOMICALLY (``_write_record``), so a
    failed or interrupted stamp — the PARENT's confirmation after the child
    stamped itself, above all — leaves each record its previous object or the
    new one, complete: the child's valid stamp is never truncated, emptied or
    cut to a different number. A failure after a record's publication
    (``PublicationUnproven``) leaves it published, never undone.
    """
    group = os.getpgrp() if pid is None else pid
    started = leader_start_time(group)
    if started is not None:
        _write_record(os.path.join(root, START_FILE), started)
    path = os.path.join(root, PGID_FILE)
    _write_record(path, str(group))
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
    os.execvp(rest[0], rest)
    return 1                                    # pragma: no cover


if __name__ == "__main__":
    sys.exit(main(sys.argv))
