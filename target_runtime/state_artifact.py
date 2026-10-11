"""The ONE hardened read of a fixed ``.herd/state`` artifact.

Moved here, unchanged, from ``target_runtime.evidence`` (I3 of task
20261010-164104-1761f6), which re-exports it under its own name, so every
existing caller and its checks are the same function. It lives in its own
module because it is shared by a READ-ONLY path that must not import the
dispatch path: ``target_runtime.legacy_result`` (the legacy Mission-Core
result recovery Grok Bot reads through). Its only dependency is the hardened
primitive in ``target_runtime.prepare`` (standard library only); this module
imports nothing else, and a test pins that.
"""

import os

from target_runtime import prepare as prepare_module


def _resolved_chain(root, subdirs):
    """The fixed subdirectory chain under the lease, or None.

    Requires every component of ``root/subdirs...`` to resolve to
    ITSELF (``os.path.realpath`` identity), so a symlinked ``.herd``,
    ``state`` or ``reviews`` component — which the separator-free
    hardened primitive cannot see — can never carry a read outside
    the lease. A missing directory resolves to itself and passes;
    the primitive then reports the file cleanly absent.
    """
    path = root
    for component in subdirs:
        path = os.path.join(path, component)
        if os.path.realpath(path) != path:
            return None
    return path


def read_state_artifact(workspace_realpath, subdirs, name):
    """One hardened read of a fixed ``.herd/state`` artifact.

    REUSES the one hardened primitive
    (``prepare.read_workspace_instruction``) for the open/read
    itself — this wrapper only (a) refuses a ``name`` carrying any
    path separator before touching the filesystem, (b) requires the
    fixed directory chain to resolve to itself before AND after the
    read (content from a chain that changed underneath the read is
    discarded), and (c) returns the primitive's exact
    ``(status, byte_count, digest, text)`` shape.
    """
    if os.sep in name or "/" in name or "\x00" in name:
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    root = os.path.realpath(workspace_realpath)
    directory = _resolved_chain(root, subdirs)
    if directory is None:
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    result = prepare_module.read_workspace_instruction(
        directory, name
    )
    if _resolved_chain(root, subdirs) != directory:
        # The chain changed while we were reading: whatever was read
        # is of unknown provenance and is DISCARDED.
        return (
            prepare_module.INSTRUCTION_REFUSED_ESCAPES,
            None, None, None,
        )
    return result
