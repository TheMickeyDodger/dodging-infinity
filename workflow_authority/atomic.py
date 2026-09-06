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

import contextlib
import fcntl
import json
import os
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
