"""Cross-process, per-Mission guard over the bridge's effects on one run.

The panel's explicit Start (a request handler) and the progression runtime (a
separate process) act on the same Mission through the same ``MissionBridge``.
Mission Core's store serializes each individual WRITE, but the bridge's
operations are read-then-act sequences, and two of them interleaving across
processes is unsafe: a progression pass that reconciled while the panel was
mid-dispatch would count an attempt against a child about to be bound, and a
verification racing an observation could decide from reads the other had
already made stale. A lock inside one process cannot stop a second process, so
the guard is an OS file lock (``flock``) on a file named for the Mission under
the panel's protected state directory. The kernel releases it when its holder
exits however it exits, so a crashed holder never leaves a stale lock to clean.

It guards EFFECTS and orders them; it grants no authority, records nothing and
decides nothing. It is not Mission Core's store lock (that one is never held
across a bridge operation) and it is not the workflow runtime's single-instance
lock (a different file, a different mode, a different state directory).

Two ways to ask: ``hold(mission_id)`` waits a bounded time and then raises
``RunGuardBusy`` (an operator's Start should wait briefly for a progression
pass, and be told plainly if it cannot), and ``hold(mission_id, blocking=False)``
answers at once with ``False`` (a progression pass SKIPS a Mission someone else
is acting on, and looks again on its next pass). Neither ever waits forever.
"""

import contextlib
import fcntl
import os
import re
import stat
import time

GUARD_DIRECTORY_NAME = "mission-run-guards"
# A guard is named by a Mission id and by nothing else: ``mn-`` and 32 lowercase
# hex digits, the grammar Mission Core mints. This module deliberately does not
# import Mission Core (it grants and decides nothing about a Mission), so the
# grammar is restated, strictly, because it becomes a file name.
_MISSION_ID = re.compile(r"mn-[0-9a-f]{32}")
GUARD_FILE_SUFFIX = ".lock"
# A blocking hold waits at most this long, polling at this interval, then
# raises ``RunGuardBusy``. Pacing between lock attempts, not a Mission timeout:
# nothing about a Mission is ever decided by it.
GUARD_WAIT_SECONDS = 30
GUARD_POLL_SECONDS = 0.05


class RunGuardRefused(Exception):
    """The guard could not be established safely (a bad id, a directory that
    is not ours, a link where a file belongs). Nothing was locked."""


class RunGuardBusy(Exception):
    """Another process holds this Mission's guard and did not let go within the
    bounded wait. Nothing was done under the guard."""


class RunGuard(object):
    """The guards under one state directory. ``wait_seconds`` exists for
    tests (a short bound); production passes nothing."""

    def __init__(self, state_directory, wait_seconds=GUARD_WAIT_SECONDS):
        self._state_directory = state_directory
        self._wait_seconds = wait_seconds

    def _directory(self):
        base = self._state_directory
        try:
            base_stat = os.stat(base)
        except OSError:
            raise RunGuardRefused(
                "the state directory for run guards is not usable")
        if not stat.S_ISDIR(base_stat.st_mode):
            raise RunGuardRefused(
                "the state directory for run guards is not a directory")
        path = os.path.join(base, GUARD_DIRECTORY_NAME)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            raise RunGuardRefused("the run guard directory cannot be created")
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RunGuardRefused(
                "the run guard directory is a link or not a directory; it is"
                " never followed")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RunGuardRefused(
                "the run guard directory is not private to this account")
        return path

    def _open(self, mission_id):
        if not isinstance(mission_id, str) or _MISSION_ID.fullmatch(mission_id) is None:
            raise RunGuardRefused("a run guard is named by a Mission id only")
        path = os.path.join(self._directory(), mission_id + GUARD_FILE_SUFFIX)
        try:
            descriptor = os.open(
                path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise RunGuardRefused(
                "the run guard file cannot be opened (a link is never"
                " followed)")
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(descriptor)
            raise RunGuardRefused("the run guard file is not a plain file of"
                                  " this account")
        return descriptor

    @contextlib.contextmanager
    def hold(self, mission_id, blocking=True):
        """Hold ``mission_id``'s guard for the body. Yields ``True``; with
        ``blocking=False`` yields ``False`` (and the body must do nothing) when
        another holder has it. A blocking hold that cannot be granted within the
        bound raises ``RunGuardBusy``."""
        descriptor = self._open(mission_id)
        held = False
        try:
            deadline = time.monotonic() + self._wait_seconds
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                    break
                except OSError:
                    if not blocking:
                        break
                    if time.monotonic() >= deadline:
                        raise RunGuardBusy(
                            "another process is acting on Mission %s; try"
                            " again shortly" % mission_id)
                    time.sleep(GUARD_POLL_SECONDS)
            yield held
        finally:
            if held:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
