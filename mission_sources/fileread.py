"""Bounded, no-follow reads of one private regular file.

The registry and every store file are read through ``read_private_file``
so that a symlink, a FIFO or other special file, an oversized file, open
permissions, or a file swapped between the check and the open all end in
an explicit refusal within an independent bound — never a hang and never
an "absent" result. Only a path that does not exist at all (``lstat``
says so) is reported as absent; a file that existed at ``lstat`` and is
gone at ``open`` is unreadable, not absent, so a race can never be read
as an empty, healthy store.

Mechanics: ``lstat`` first (refuse symlink / non-regular); open with
``O_RDONLY | O_NOFOLLOW | O_NONBLOCK`` (a symlink raced in fails with
``ELOOP``; a FIFO raced in does not block); ``fstat`` the descriptor and
require the SAME device and inode as the ``lstat``, a regular file, no
group/other permission bits and a size within the bound; then read in
chunks, stopping at end of file or refusing once the bound is exceeded.
Every loop iteration either consumes at least one byte or ends.
"""

import errno
import json
import os
import stat

READ_CHUNK_BYTES = 65536
# JSON nesting allowed in any file this package parses. A byte bound does not
# bound the parser's recursion, so depth is checked by a non-recursive scan
# BEFORE ``json.loads`` runs.
MAX_JSON_DEPTH = 64

PROBLEM_FILE_ABSENT = "file_absent"
PROBLEM_FILE_SYMLINK = "file_is_symlink"
PROBLEM_FILE_NOT_REGULAR = "file_not_regular"
PROBLEM_FILE_TOO_LARGE = "file_exceeds_read_bound"
PROBLEM_FILE_PERMISSIONS_OPEN = "file_permissions_open"
PROBLEM_FILE_UNREADABLE = "file_unreadable"
PROBLEM_JSON_NOT_UTF8 = "json_not_utf8"
PROBLEM_JSON_INVALID = "json_invalid"
PROBLEM_JSON_TOO_DEEP = "json_nesting_exceeds_bound"

_OPEN_MODE_BITS = 0o077


class FileRefused(Exception):
    """The file was not read; ``problem`` is one of the codes above."""

    def __init__(self, problem, path):
        super(FileRefused, self).__init__("%s: %s" % (problem, path))
        self.problem = problem


def read_private_file(path, max_bytes):
    """Return the bytes of the private regular file at ``path``."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise FileRefused(PROBLEM_FILE_ABSENT, path)
    except OSError:
        raise FileRefused(PROBLEM_FILE_UNREADABLE, path)
    if stat.S_ISLNK(before.st_mode):
        raise FileRefused(PROBLEM_FILE_SYMLINK, path)
    if not stat.S_ISREG(before.st_mode):
        raise FileRefused(PROBLEM_FILE_NOT_REGULAR, path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise FileRefused(PROBLEM_FILE_SYMLINK, path)
        raise FileRefused(PROBLEM_FILE_UNREADABLE, path)
    try:
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            raise FileRefused(PROBLEM_FILE_NOT_REGULAR, path)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise FileRefused(PROBLEM_FILE_UNREADABLE, path)
        if stat.S_IMODE(after.st_mode) & _OPEN_MODE_BITS:
            raise FileRefused(PROBLEM_FILE_PERMISSIONS_OPEN, path)
        if after.st_size > max_bytes:
            raise FileRefused(PROBLEM_FILE_TOO_LARGE, path)
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor,
                            min(READ_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FileRefused(PROBLEM_FILE_TOO_LARGE, path)
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        raise FileRefused(PROBLEM_FILE_UNREADABLE, path)
    finally:
        os.close(descriptor)


class JsonRefused(ValueError):
    """The bytes were not parsed; ``problem`` is one of the json codes."""

    def __init__(self, problem):
        super(JsonRefused, self).__init__(problem)
        self.problem = problem


def nesting_depth_within(text, max_depth):
    """True when no object/array nests deeper than ``max_depth``.

    A single linear pass that tracks only whether it is inside a string (and
    after a backslash there); it never recurses, so hostile input cannot
    exhaust the stack here. It does not validate JSON; ``json.loads`` does
    that afterwards, within the proven depth."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif char in "]}":
            depth -= 1
    return True


def load_bounded_json(data, max_depth=MAX_JSON_DEPTH):
    """Parse UTF-8 JSON bytes whose nesting is proven within ``max_depth``
    first; every failure is a ``JsonRefused`` with a stable problem code."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonRefused(PROBLEM_JSON_NOT_UTF8)
    if not nesting_depth_within(text, max_depth):
        raise JsonRefused(PROBLEM_JSON_TOO_DEEP)
    try:
        return json.loads(text)
    except RecursionError:
        raise JsonRefused(PROBLEM_JSON_TOO_DEEP)
    except ValueError:
        raise JsonRefused(PROBLEM_JSON_INVALID)
