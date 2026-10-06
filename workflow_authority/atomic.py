"""The atomic-replace write and the cross-process store lock, stdlib only.

Extracted verbatim from ``workflow_authority.store`` (which still
re-exports both names unchanged) so that a store which must load NO
provider in its import closure — the neutral Mission Core — can share
the ONE atomic-replace primitive and the ONE lock primitive instead of
copying them. ``workflow_authority.store`` itself imports the adapter
configuration for its default directory; this module imports nothing
outside the standard library and defines no default directory.

``atomic_write_json``: temp file created in the same directory,
``fchmod`` 600, ``json.dump``, flush, ``fsync``, ``os.replace``, then an
fsync of the directory. A crash never leaves a torn file and an
interrupted write leaves the previous file byte-identical. The caller
validates the document before calling this — nothing here validates.

``exclusive_store_lock``: a blocking ``flock`` on a lock file separate
from the store file, so ``os.replace`` never invalidates the held
descriptor. Every writer holds it around its whole load-modify-save
cycle; each store passes its own lock file name so distinct stores
never serialize against each other.
"""

import collections
import contextlib
import fcntl
import json
import os
import stat
import tempfile

# The workflow store's lock file name; kept here so the default of
# ``exclusive_store_lock`` is byte-identical to what it always was.
WORKFLOWS_LOCK_FILE_NAME = "workflows.lock"


def atomic_write_json(directory, path, document, temp_prefix):
    """The ONE atomic-replace primitive for every protected store.

    Temp file created in the same directory, ``fchmod`` 600,
    ``json.dump``, flush, ``fsync``, ``os.replace``, then an fsync of
    the directory. A crash never leaves a torn file and an interrupted
    write leaves the previous file byte-identical. Extracted verbatim
    from ``WorkflowStore.save`` (P1-A6) so the sibling PR delivery
    store shares it instead of copying it; the caller validates the
    document before calling this — nothing here validates.
    """
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=temp_prefix, suffix=".tmp", dir=directory
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


# -- the observer read result (Task 8, slice S-II) ------------------------
#
# A store OWNER's ``read()`` returns one of these from ONE authoritative
# document read through its own validator: PRESENT with the validated
# document; ABSENT only when the file is genuinely missing with every
# ancestor accessible (``path_access``); UNAVAILABLE for every access or
# read error (permission, a symbolic link whose followed target is
# missing or inaccessible, ENOTDIR or any other OSError, invalid content
# refused by the validator), named by class or reason. It is read-only,
# takes no lock and creates nothing; writers keep using ``load``.

READ_PRESENT = "present"
READ_ABSENT = "absent"
READ_UNAVAILABLE = "unavailable"
READ_AVAILABILITIES = (READ_ABSENT, READ_PRESENT, READ_UNAVAILABLE)

ReadResult = collections.namedtuple("ReadResult",
                                    ("availability", "document", "problem"))


SYMLINK_TARGET_MARK = " (symlink target)"
# The file-relative open raised FileNotFoundError, yet an entry exists at
# the following lstat: either a dangling link inside the directory or a
# regular entry that appeared between the two calls (a race). Neither is
# a proven absence; the description is neutral about which (R13-C2).
ENTRY_AFTER_FAILED_OPEN = ("FileNotFoundError (entry present after a failed"
                           " open: race or dangling link)")


def _text_path(path):
    """The path as text: ``str`` or ``os.PathLike`` (``os.fspath``), the
    same inputs every owner's ``load`` accepts through ``os.path.join``;
    ``bytes`` is refused with ``TypeError`` exactly as the owners'
    ``os.path.join(directory, <str name>)`` refuses it."""
    path = os.fspath(path)
    if not isinstance(path, str):
        raise TypeError("a store path must be str or os.PathLike[str], not %s"
                        % type(path).__name__)
    return path


def _literal_prefixes(path):
    """The prefixes of ``path`` AS GIVEN, one per component, in the order
    the filesystem traverses them: no normalization, so ``..`` and ``.``
    components stay where they are and are resolved by the OS against
    the component before them (a ``..`` after a dangling link is reached
    THROUGH that link, exactly as ``open`` would reach it). A relative
    path is walked from the current directory; a trailing separator
    yields the directory itself as the last prefix. ``str`` or
    ``os.PathLike`` (``_text_path``)."""
    path = _text_path(path)
    prefixes = []
    prefix = ""
    for part in path.split(os.sep):
        if prefix == "" and part == "":
            prefix = os.sep  # the root of an absolute path
            continue
        if part == "":
            continue  # a doubled or trailing separator adds no component
        prefix = part if prefix == "" else (
            prefix + part if prefix.endswith(os.sep) else prefix + os.sep + part)
        prefixes.append(prefix)
    if not prefixes:
        prefixes.append(prefix or os.curdir)
    return prefixes


def path_access(path):
    """Read-only evidence about ``path`` AS THE FILESYSTEM TRAVERSES IT:
    every literal prefix of the path as given (``..`` and ``.``
    components included, nothing collapsed) is ``lstat``-ed in
    traversal order and, where it is a symbolic link, its FOLLOWED
    target ``stat``-ed too. Returns ``(READ_PRESENT, None)``,
    ``(READ_ABSENT, None)`` (a component is genuinely missing and every
    component before it was reachable) or ``(READ_UNAVAILABLE,
    <reason>)`` for any other failure: a dangling or inaccessible link
    target, a non-directory in the middle of the path (``ENOTDIR``), a
    permission refusal, any other ``OSError``. ``path`` is ``str`` or
    ``os.PathLike`` (``_text_path``)."""
    for prefix in _literal_prefixes(_text_path(path)):
        try:
            info = os.lstat(prefix)
        except FileNotFoundError:
            return READ_ABSENT, None
        except OSError as exc:
            return READ_UNAVAILABLE, type(exc).__name__
        if stat.S_ISLNK(info.st_mode):
            try:
                os.stat(prefix)
            except FileNotFoundError:
                return READ_UNAVAILABLE, "FileNotFoundError" + SYMLINK_TARGET_MARK
            except OSError as exc:
                return READ_UNAVAILABLE, type(exc).__name__ + SYMLINK_TARGET_MARK
    return READ_PRESENT, None


def classify_missing(path):
    """After ONE read attempt raised ``FileNotFoundError``: the store is
    ABSENT only if the path is genuinely missing with accessible
    ancestors; otherwise the read failed access (a dangling or
    inaccessible link, a vanished ancestor) and it is UNAVAILABLE."""
    availability, problem = path_access(path)
    if availability == READ_ABSENT:
        return ReadResult(READ_ABSENT, None, None)
    return ReadResult(READ_UNAVAILABLE, None,
                      problem or "FileNotFoundError (during the read)")


# The observer read validates WHAT WAS ACTUALLY OPENED (never a path
# checked beforehand): the rules below name the refusal in the result.
STORE_EXPOSURE_BITS = 0o077
RULE_FILE_EXPOSED = "store file is accessible by group/other (mode %o)"
RULE_DIRECTORY_EXPOSED = "store directory is accessible by group/other (mode %o)"
RULE_NOT_REGULAR_FILE = "store path is not a regular file"
RULE_INVALID_UTF8 = "invalid UTF-8"
RULE_INVALID_JSON = "invalid JSON"
RULE_JSON_TOO_DEEP = "JSON nesting too deep (RecursionError)"


READ_BLOCK_BYTES = 65536


def _unavailable(problem):
    return ReadResult(READ_UNAVAILABLE, None, problem)


def _close_once(descriptor):
    """The ONE close of a descriptor this module owns; a failing close is
    reported as a problem (by class), never raised."""
    try:
        os.close(descriptor)
    except OSError as exc:
        return type(exc).__name__
    return None


def _directory_problem(directory_descriptor, error_name, refuse_exposed):
    """The opened directory re-examined through its descriptor: the
    owner's protected-directory rule when it has one."""
    try:
        info = os.fstat(directory_descriptor)
    except OSError as exc:
        return type(exc).__name__
    if refuse_exposed and info.st_mode & STORE_EXPOSURE_BITS:
        return "%s: %s" % (error_name,
                           RULE_DIRECTORY_EXPOSED % stat.S_IMODE(info.st_mode))
    return None


def _read_admitted_file(descriptor, error_name):
    """``(data, problem)`` from an OPENED file descriptor: the opened
    target must be a regular file group/other cannot reach; then every
    byte is read from that descriptor. The caller owns the descriptor;
    nothing here closes it."""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None, "%s: %s" % (error_name, RULE_NOT_REGULAR_FILE)
        if info.st_mode & STORE_EXPOSURE_BITS:
            return None, "%s: %s" % (
                error_name, RULE_FILE_EXPOSED % stat.S_IMODE(info.st_mode))
        blocks = []
        while True:
            block = os.read(descriptor, READ_BLOCK_BYTES)
            if not block:
                return b"".join(blocks), None
            blocks.append(block)
    except OSError as exc:
        return None, type(exc).__name__


def _parse_document(data, error_name):
    """The bytes decoded as UTF-8 and parsed as JSON; every decode or
    parse failure is a named result, never an exception."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _unavailable("%s: %s" % (error_name, RULE_INVALID_UTF8))
    try:
        document = json.loads(text)
    except RecursionError:
        return _unavailable("%s: %s" % (error_name, RULE_JSON_TOO_DEEP))
    except ValueError:
        return _unavailable("%s: %s" % (error_name, RULE_INVALID_JSON))
    except Exception as exc:  # noqa: BLE001 - a parse failure never escapes
        return _unavailable("%s: %s" % (error_name, type(exc).__name__))
    return ReadResult(READ_PRESENT, document, None)


def _admit_document(directory_descriptor, file_name, error_name,
                    refuse_exposed_directory):
    """Steps 1b-3 of ``read_store_document`` over the OPENED directory
    descriptor (owned and closed by the caller)."""
    problem = _directory_problem(directory_descriptor, error_name,
                                 refuse_exposed_directory)
    if problem is not None:
        return _unavailable(problem)
    try:
        descriptor = os.open(file_name, os.O_RDONLY | os.O_NONBLOCK,
                             dir_fd=directory_descriptor)
    except FileNotFoundError:
        try:
            os.lstat(file_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            # Genuine absence inside the opened directory — admitted only
            # after the same final directory-policy recheck the present
            # branch performs (an owner with a directory policy refuses
            # an exposure change before classification completes).
            if refuse_exposed_directory:
                problem = _directory_problem(directory_descriptor, error_name,
                                             True)
                if problem is not None:
                    return _unavailable(problem)
            return ReadResult(READ_ABSENT, None, None)
        except OSError as exc:
            return _unavailable(type(exc).__name__)
        return _unavailable(ENTRY_AFTER_FAILED_OPEN)
    except OSError as exc:
        return _unavailable(type(exc).__name__)
    # ``descriptor`` is owned HERE and closed exactly once, by
    # ``_close_once`` below, on success and on every failure alike;
    # ``_read_admitted_file`` never closes it.
    try:
        data, problem = _read_admitted_file(descriptor, error_name)
    finally:
        close_problem = _close_once(descriptor)
    if problem is None:
        problem = close_problem
    if problem is not None:
        return _unavailable(problem)
    # Directory boundary across admission: an owner with a directory
    # policy re-validates the OPENED directory after the file was read
    # and before the document is admitted, so an exposure change in
    # between refuses.
    if refuse_exposed_directory:
        problem = _directory_problem(directory_descriptor, error_name, True)
        if problem is not None:
            return _unavailable(problem)
    return _parse_document(data, error_name)


def read_store_document(directory, file_name, error_name,
                        refuse_exposed_directory):
    """ONE authoritative document read of ``<directory>/<file_name>``
    through descriptors, validating what was actually opened:

    1. the store DIRECTORY is opened (``O_RDONLY | O_DIRECTORY``; a
       symbolic link to a directory is followed) and ``fstat``-ed on the
       opened descriptor — with ``refuse_exposed_directory`` the owner's
       protected-directory rule (group/other must not reach it) applies
       to that opened target, and is applied AGAIN to the same
       descriptor after the file was read, before the document is
       admitted;
    2. the store FILE is opened RELATIVE TO that descriptor (``dir_fd``;
       a symbolic link is followed, ``O_NONBLOCK`` so a FIFO cannot
       stall the read) and ``fstat``-ed on the opened descriptor: it
       must be a regular file that group/other cannot reach — the same
       file-mode rule every owner's ``load`` enforces, applied to the
       opened target instead of a path checked earlier;
    3. the bytes are read from that descriptor only, decoded as UTF-8
       and parsed as JSON; every decode or parse failure (invalid UTF-8,
       invalid JSON, nesting past the recursion limit, anything else)
       is a named result, never an exception.

    Descriptor ownership is single and explicit: each of the two
    descriptors is closed exactly once by this module (``_close_once``),
    on success and on every failure, never retried, and a failing close
    turns ANY non-refused result (PRESENT or ABSENT) into a named
    UNAVAILABLE (a refusal already established keeps its own problem).
    An owner with a directory policy rechecks the opened directory
    descriptor before admitting the document AND before admitting
    absence. ``directory`` is ``str`` or ``os.PathLike`` (the inputs the
    owners' ``load`` accepts; ``bytes`` is refused with ``TypeError`` as
    ``os.path.join`` refuses it). Returns ``(availability, document,
    problem)``: PRESENT with the parsed (not yet validated) document;
    ABSENT only when the directory or the file is genuinely missing
    (``classify_missing`` for the directory; an ``lstat`` relative to the
    opened directory for the file, so a dangling link inside it is
    UNAVAILABLE); UNAVAILABLE with the refusing rule prefixed by
    ``error_name``, or the ``OSError`` class. No lock is taken and
    nothing is created. Every path is enumerated in the slice's
    evidence (correction 3) with its covering test."""
    directory = _text_path(directory)
    try:
        directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return classify_missing(directory)
    except OSError as exc:
        return _unavailable(type(exc).__name__)
    try:
        result = _admit_document(directory_descriptor, file_name, error_name,
                                 refuse_exposed_directory)
    finally:
        close_problem = _close_once(directory_descriptor)
    if close_problem is not None and result.availability != READ_UNAVAILABLE:
        return _unavailable(close_problem)
    return result


@contextlib.contextmanager
def exclusive_store_lock(directory, lock_file_name=WORKFLOWS_LOCK_FILE_NAME):
    """Blocking cross-process lock over the workflow store.

    Hold this around every load-modify-save cycle. The lock file is
    separate from the store file so ``os.replace`` never invalidates
    the held descriptor. ``lock_file_name`` defaults to the workflow
    store's lock; the sibling PR delivery store passes its own name so
    the two stores never serialize against each other.
    """
    os.makedirs(directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(directory, lock_file_name)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
