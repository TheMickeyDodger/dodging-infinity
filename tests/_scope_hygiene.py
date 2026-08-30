"""TEST ISOLATION for the machine-global scope stores. NO DELETION.

What this file used to be, and why it is not that today
======================================================

It held `remove_additions`, which computed ``entries() - before`` over
the SHARED bases and removed the difference. R-47 named the class:
APPEARED-DURING IS NOT OWNED-BY. A Runtime running on the same machine
creates its scopes during a test case, and those are exactly the
entries a set difference selects — so a harness written to tidy up
after itself would remove another party's records. Same family as
attribution by directory NAME (R-43) and check-then-use (R-40): the
thing removed was not proven to be the thing created. The
live-group guard narrowed the blast radius and established no
ownership; a finished unrelated scope still qualified.

The irony is worth keeping on the record: within this increment the
subject is never removing unrelated resources, and its harness removed
them.

What replaced it
================

ISOLATION, not a better guard. `process_ownership.default_base` is one
seam that both stores resolve through, and a test process redirects it
to a PRIVATE directory. Within such a process the suite has no shared
store to clean, and the destructive capability is unrepresentable
rather than guarded — the standard already applied when `close_fn` was
given no default and when `required_names` lost its ``()``.

Unisolated access RAISES (`SharedBaseReached`). That is what makes the
enumeration mechanical: a test path that reaches the shared store
fails loudly and names itself, instead of being found by reading.

What this module will not do
============================

Within this module no `rmtree`, `unlink` or `remove` targets a shared
base. Within this module the census helpers open nothing for writing.
The remaining records under the real bases are EVIDENCE; their
disposition is a separate, explicitly authorized action.
"""

import hashlib
import os
import shutil
import tempfile

from target_runtime import process_ownership as _own


class SharedBaseReached(AssertionError):
    """A test path resolved the MACHINE-GLOBAL scope store."""


#: The isolation stack. A PROCESS-WIDE global, not thread-local, and
#: the difference is not cosmetic: the suite drives run loops and
#: worker drains on other threads, and a thread-local base left those
#: threads unisolated — the guard fired inside `cli.main` and inside
#: the Telegram adapter's worker. Isolation is a property of the
#: PROCESS's temp layout, so it belongs to the process.
_STACK = []
_ACTIVE = [None]

#: The shared bases as they stood when the suite began importing test
#: modules — a CONTENT snapshot, not a name list (R-51 AO-1). Read
#: ONCE and read-only; within this module it is never written back. It
#: is the baseline the end-of-run census compares against (AL-2), and
#: the witness that unrelated records survived byte-for-byte (AL-3).
SUITE_START = None


#: BOUNDS on the content snapshot, stated exactly because AO-5 asks
#: for the floor rather than a claim of completeness.
#:
#: A snapshot that walked without limit would turn suite startup into
#: an unbounded read of whatever has accumulated on the machine. These
#: caps are what keep it bounded, and a snapshot that hits one says so
#: in its own `truncated` field rather than reading as complete.
MAX_SNAPSHOT_ENTRIES = 4096
MAX_TREE_FILES = 256
MAX_FILE_BYTES = 1048576


def shared_root():
    """The REAL temp root, resolved WITHOUT `default_base`.

    The guard makes `default_base` raise, and the census has to be
    able to look at the very store the suite is forbidden to touch.
    """
    return tempfile.gettempdir()


def shared_base_entries(root=None):
    """``(store, name)`` NAME PAIRS for the real scope stores.

    IDENTITY ONLY, and the name says so. R-51: within this result a
    name cannot witness a byte — a path that overwrites a preexisting
    record in place changes no name. Kept because identity is still
    necessary for detecting additions and removals;
    `shared_base_snapshot` is what adds the content half.
    """
    return set(shared_base_snapshot(root)["entries"])


def _digest_file(path, digest, bounded):
    """Fold one file's TYPE, SIZE and BYTES into ``digest``.

    Reads only. AM-1 binds: within this helper no shared path is
    opened for writing, and no marker is written to make comparison
    easier.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        digest.update(b"\0unreadable")
        return
    digest.update(b"\0f%d\0" % size)
    read = 0
    try:
        with open(path, "rb") as handle:
            while read < MAX_FILE_BYTES:
                chunk = handle.read(min(65536, MAX_FILE_BYTES - read))
                if not chunk:
                    break
                digest.update(chunk)
                read += len(chunk)
    except OSError:
        digest.update(b"\0unreadable")
        return
    if size > MAX_FILE_BYTES:
        bounded.append(path)


def _entry_content(path, bounded):
    """``(type, digest)`` for one shared entry.

    A DIRECTORY's content is the digest of its sorted tree: every
    contained name, plus each file's size and bytes. That is what
    makes a mutation IN PLACE — same directory name, changed pgid or
    nonce file inside it — visible to the census.
    """
    digest = hashlib.sha256()
    if os.path.islink(path):
        try:
            digest.update(os.readlink(path).encode("utf-8", "replace"))
        except OSError:
            digest.update(b"\0unreadable")
        return "link", digest.hexdigest()
    if os.path.isfile(path):
        _digest_file(path, digest, bounded)
        return "file", digest.hexdigest()
    if not os.path.isdir(path):
        return "other", digest.hexdigest()
    seen = 0
    for current, directories, files in os.walk(path):
        directories.sort()
        relative = os.path.relpath(current, path)
        digest.update(b"\0d\0" + relative.encode("utf-8", "replace"))
        for name in sorted(files):
            if seen >= MAX_TREE_FILES:
                bounded.append(current)
                return "dir", digest.hexdigest()
            digest.update(b"\0n\0" + name.encode("utf-8", "replace"))
            _digest_file(os.path.join(current, name), digest, bounded)
            seen += 1
    return "dir", digest.hexdigest()


def shared_base_snapshot(root=None):
    """A bounded, deterministic CONTENT snapshot of the real stores.

    AO-1. Returns ``{"entries": {(store, name): (type, digest)},
    "truncated": bool, "bounded": [paths]}``.

    WHY CONTENT AND NOT NAMES: AL-3's guarantee is that an unrelated
    preexisting record survives BYTE-IDENTICALLY. A name set detects
    a record being REMOVED and is blind to one being OVERWRITTEN in
    place, so a census built on names satisfied a weaker reading of
    the ruling than the ruling's own words. The digest is what lets
    the comparison mean what AL-3 says.

    READS ONLY. Within this function no write reaches a shared store —
    not a marker, not a lock file, not a temp file (AO-3/AM-1).

    BOUNDS, stated rather than implied (AO-5). Within one snapshot:
    at most `MAX_SNAPSHOT_ENTRIES` entries; at most `MAX_TREE_FILES`
    files per directory tree; at most `MAX_FILE_BYTES` of one file
    folded into its digest. When a cap bites, `truncated` or
    `bounded` says so, and a comparison over a truncated snapshot
    covers what it covered — not the store.
    """
    base = shared_root() if root is None else root
    entries, bounded = {}, []
    truncated = False
    for store in (_own.OWNED_ROOT_DIR_NAME, _own.ASSIGNMENT_DIR_NAME):
        directory = os.path.join(base, store)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if len(entries) >= MAX_SNAPSHOT_ENTRIES:
                truncated = True
                break
            entries[(store, name)] = _entry_content(
                os.path.join(directory, name), bounded
            )
    return {
        "entries": entries,
        "truncated": truncated,
        "bounded": sorted(bounded),
    }


def compare_snapshots(before, after):
    """``(added, removed, mutated)`` — identity AND content.

    ``mutated`` is the half that a name-set census, within its own
    terms, is unable to express: an entry present in both snapshots whose ``(type,
    digest)`` differs. It is reported with both digests, so a failure
    names what changed rather than only that something did.
    """
    old, new = before["entries"], after["entries"]
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    mutated = sorted(
        (key, old[key], new[key])
        for key in set(old) & set(new)
        if old[key] != new[key]
    )
    return added, removed, mutated


def _guarded_default_base():
    active = _ACTIVE[0]
    if active is None:
        raise SharedBaseReached(
            "a test path resolved the MACHINE-GLOBAL scope store."
            " Every test that reaches spawn_owned, create_owned_root"
            " or assign_scope — directly or through a production seam"
            " such as run_role_turn or run_planning_turn — must run"
            " under _scope_hygiene.isolate_case/isolate_module, so the"
            " suite writes to a PRIVATE base and has nothing shared to"
            " clean up afterwards (R-47/R-48)"
        )
    return active


def install_guard():
    """Make the shared store unreachable from this process."""
    global SUITE_START
    if SUITE_START is None:
        SUITE_START = shared_base_snapshot()
    if _own.default_base is not _guarded_default_base:
        _own.default_base = _guarded_default_base


def _stack():
    return _STACK


def _open_private():
    """Push a private base. A STACK, not a slot.

    A slot was wrong and the suite proved it within one run: a
    module-level isolation and a per-case isolation both write here,
    and the case's cleanup used to clear the slot outright — leaving
    every later test in that module unisolated and tripping the
    guard. Restoring the PREVIOUS value keeps the two nested.
    """
    directory = tempfile.mkdtemp(prefix="di-isolated-scope-")
    _STACK.append(directory)
    _ACTIVE[0] = directory
    return directory


def _close_private(directory):
    if directory in _STACK:
        _STACK.remove(directory)
    _ACTIVE[0] = _STACK[-1] if _STACK else None
    # The private base is this case's own, so within this cleanup
    # nothing shared is removed. It is still kept whenever a group recorded
    # inside it is ALIVE: deleting a live process's ownership record
    # is the one act the whole module exists to prevent, and a
    # fixture is not exempt from it.
    for scope in _private_scopes(directory):
        if _own.scope_has_live_group(scope):
            return False
    if _own.surviving_owned_groups(directory):
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def _private_scopes(directory):
    base = os.path.join(directory, _own.OWNED_ROOT_DIR_NAME)
    if not os.path.isdir(base):
        return []
    return [
        os.path.join(base, name)
        for name in os.listdir(base)
        if os.path.isdir(os.path.join(base, name))
    ]


def isolate_case(case):
    """Give ONE test case its own private scope stores."""
    install_guard()
    directory = _open_private()
    case.addCleanup(_close_private, directory)
    return directory


def isolate_module():
    """Give a module its own private scope stores (`setUpModule`)."""
    install_guard()
    return _open_private()


def release_module(directory):
    return _close_private(directory)
