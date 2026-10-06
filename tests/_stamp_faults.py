"""Task 8 R27-1: controlled FAULTS inside the stamp writer — test fixture only.

``StampFaults`` stands in for ``spawn_stamp``'s ``os`` module while it is
active (``active()``). Every attribute is the real ``os``'s, except the calls a
case ARMS. Only a writer running in THIS interpreter sees it: a child stamps
itself with its own interpreter, so a fault armed here reaches the PARENT's
confirmation and never the child's own stamp.

A fault names the RECORD it applies to (``pgid``, ``leader-start``): a write,
``fsync`` or close is matched by the record its descriptor was opened FOR — a
replacement beside that record (``spawn_stamp.REPLACEMENT_PREFIX``) or, under a
writer that writes in place, the record itself. So one armed fault means the
same thing under the R27-1 writer and under the truncating writer it replaced.
Nothing here signals, waits or removes anything outside the case's own tree."""

import errno
import os
import stat
from unittest import mock

from target_runtime import spawn_stamp


class Killed(BaseException):
    """A writer stopped where it stands, as by a kill: no ``Exception``
    handler sees it."""


class StampFaults(object):
    """``spawn_stamp``'s ``os``, with the armed calls made to fail (``arm``).
    ``calls`` lists, in order, every write / fsync / rename / unlink / lstat
    the writer made through it; ``paths`` maps each descriptor it opened to
    the path it was opened as."""

    TRACED = ("write", "fsync", "rename", "unlink", "lstat", "close", "link")

    def __init__(self):
        self.paths, self.calls, self.armed = {}, [], {}

    def __getattr__(self, name):
        real = getattr(os, name)
        if name == "open":
            return self._open
        if name not in self.TRACED:
            return real
        fault = self.armed.get(name)

        def call(*args, **kwargs):
            self.calls.append((name, args[0] if args else None))
            if fault is not None:
                return fault(real, *args, **kwargs)
            return real(*args, **kwargs)
        return call

    def _open(self, path, flags, *args, **kwargs):
        descriptor = os.open(path, flags, *args, **kwargs)
        self.paths[descriptor] = path
        return descriptor

    def active(self):
        return mock.patch.object(spawn_stamp, "os", self)

    def arm(self, name, fault):
        self.armed[name] = fault
        return self

    def record_of(self, descriptor):
        """The record a descriptor was opened FOR: its replacement's or its own
        name — or None for a directory, or a descriptor not opened here."""
        path = self.paths.get(descriptor)
        if path is None:
            return None
        name = os.path.basename(path)
        if name.startswith(spawn_stamp.REPLACEMENT_PREFIX):
            return name[len(spawn_stamp.REPLACEMENT_PREFIX):].rsplit("-", 1)[0]
        return name

    def is_directory(self, descriptor):
        return stat.S_ISDIR(os.fstat(descriptor).st_mode)

    def renamed(self):
        return any(name == "rename" for name, _detail in self.calls)

    # -- the faults ----------------------------------------------------------------

    def short_write(self, record, keep, number=errno.EIO):
        """The FIRST write for ``record`` writes only its first ``keep`` bytes
        (a negative ``keep``: all but that many, at least one); the next write
        for it fails (``number``) — a SHORT WRITE then an I/O failure."""
        state = {"short": False}

        def write(real, descriptor, data):
            if self.record_of(descriptor) != record:
                return real(descriptor, data)
            if not state["short"]:
                state["short"] = True
                data = bytes(data)
                count = keep if keep >= 0 else max(1, len(data) + keep)
                return real(descriptor, data[:count])
            raise OSError(number, os.strerror(number))
        return self.arm("write", write)

    def failed_write(self, record, number=errno.ENOSPC):
        """Every write for ``record`` fails (``number``) before any byte."""
        def write(real, descriptor, data):
            if self.record_of(descriptor) == record:
                raise OSError(number, os.strerror(number))
            return real(descriptor, data)
        return self.arm("write", write)

    def failed_record_fsync(self, record, number=errno.EIO):
        """The ``fsync`` of what was written for ``record`` fails."""
        def fsync(real, descriptor):
            if not self.is_directory(descriptor) and self.record_of(descriptor) == record:
                raise OSError(number, os.strerror(number))
            return real(descriptor)
        return self.arm("fsync", fsync)

    def failed_directory_fsync(self, number=errno.EIO, after=0):
        """A DIRECTORY's ``fsync`` fails, after ``after`` that succeed."""
        state = {"seen": 0}

        def fsync(real, descriptor):
            if self.is_directory(descriptor):
                state["seen"] += 1
                if state["seen"] > after:
                    raise OSError(number, os.strerror(number))
            return real(descriptor)
        return self.arm("fsync", fsync)

    def failed_rename(self, number=errno.EIO):
        """The rename fails (``number``), and changes nothing."""
        def rename(real, source, target):
            raise OSError(number, os.strerror(number))
        return self.arm("rename", rename)

    def before_rename(self, action):
        """``action(source, target)`` runs, then the real rename."""
        def rename(real, source, target):
            action(source, target)
            return real(source, target)
        return self.arm("rename", rename)

    def killed_at_rename(self, after=0):
        """The writer is stopped at a rename — after ``after`` renames that
        run — with no chance to discard what it made: that rename never runs,
        and its cleanup's unlink fails too."""
        state = {"seen": 0}

        def rename(real, source, target):
            state["seen"] += 1
            if state["seen"] <= after:
                return real(source, target)
            raise Killed("stopped at the rename")

        def unlink(real, path):
            raise OSError(errno.EIO, "the writer is gone")
        return self.arm("rename", rename).arm("unlink", unlink)

    def after_publication(self, action):
        """``action(path)`` runs at the writer's FIRST ``lstat`` after its rename
        (the confirmation), then the real ``lstat``."""
        state = {"done": False}

        def lstat(real, path, *args, **kwargs):
            if self.renamed() and not state["done"]:
                state["done"] = True
                action(path)
            return real(path, *args, **kwargs)
        return self.arm("lstat", lstat)


def replacements_in(directory):
    """The replacement names left in ``directory`` (none, once a writer
    finished or discarded)."""
    return sorted(name for name in os.listdir(directory)
                  if name.startswith(spawn_stamp.REPLACEMENT_PREFIX))
