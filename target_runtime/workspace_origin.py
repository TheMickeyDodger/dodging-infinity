"""The GitHub repository a workspace's ``origin`` remote names.

Read-only, bounded, and local: one ``.git/config`` (or the gitdir it points at,
including a worktree's ``commondir``). No ``git`` process is started, and a
workspace that has no readable origin is ``None``, never a guessed URL.

The key is ``workflow_authority.canonical.repository_identity_key`` of the
suffix-free canonical URL, the same identity a delivery record's
``repository.repository_url`` is checked against.
"""

import os
import stat

from workflow_authority import canonical

_MAX_CONFIG_BYTES = 8192


def repository_identity(url):
    """The repository identity of ``url``, or None when it is not a canonical
    GitHub repository URL (an optional ``.git`` suffix is stripped first)."""
    if type(url) is not str or not url:
        return None
    stripped = url[:-4] if url.endswith(".git") else url
    try:
        target = canonical.canonicalize_repository_url(stripped)
    except canonical.CanonicalizationError:
        return None
    return canonical.repository_identity_key(target)


def origin_repository_url(workspace):
    """The origin remote's repository identity for ``workspace``, or None.

    None covers a missing path, a missing or unreadable config, no origin
    remote, and a URL that is not a canonical GitHub repository. Nothing is
    written and no process is started."""
    try:
        return _origin(workspace)
    except (OSError, UnicodeError, ValueError):
        return None


def workspace_git_dir(workspace):
    """The realpath of the git directory THIS workspace owns, or None.

    For a plain clone that is its ``.git`` directory; for a linked worktree it
    is the per-worktree directory its ``.git`` file names (``.git/worktrees/
    <name>``), which is NOT shared with the other worktrees of the same origin.
    It is the value a delivery record seals as ``repository.git_dir_realpath``.
    Read-only, bounded and local, like ``origin_repository_url``: no process is
    started, and a workspace with no readable git directory is None, never a
    guess."""
    try:
        return _workspace_git_dir(workspace)
    except (OSError, UnicodeError, ValueError):
        return None


def _workspace_git_dir(workspace):
    if type(workspace) is not str or not os.path.isabs(workspace):
        return None
    git_path = os.path.join(os.path.realpath(workspace), ".git")
    if os.path.isdir(git_path):
        return os.path.realpath(git_path)
    if not os.path.isfile(git_path):
        return None
    return _gitdir(git_path, _read_guarded(git_path))


def _read_guarded(path):
    """The text of a small regular file, read the guarded way: ONE O_NOFOLLOW | O_NONBLOCK open (a symlink is refused, and a FIFO opens instead
    of waiting for a writer so the check below actually runs), an fstat of the DESCRIPTOR that must say regular file, and a read of at most
    the bound plus one byte from that same descriptor. Raises OSError or ValueError, which the callers treat as "not read"."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("not a regular file")
        data = os.read(descriptor, _MAX_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("git file is over the read bound")
    return data.decode("utf-8")


def _origin(workspace):
    if type(workspace) is not str or not os.path.isabs(workspace):
        return None
    config = _config_path(os.path.join(os.path.realpath(workspace), ".git"))
    if config is None:
        return None
    return repository_identity(_origin_url(_read_bounded(config)))


def _config_path(git_path):
    """``config`` for a ``.git`` directory, or for a gitdir file (one
    worktree hop through ``commondir``). None when that file is not there."""
    if os.path.isdir(git_path):
        config = os.path.join(git_path, "config")
        return config if os.path.isfile(config) else None
    if not os.path.isfile(git_path):
        return None
    gitdir = _gitdir(git_path, _read_bounded(git_path))
    if gitdir is None:
        return None
    common_file = os.path.join(gitdir, "commondir")
    if os.path.isfile(common_file):
        common = _read_bounded(common_file).strip()
        if common:
            base = common if os.path.isabs(common) else os.path.join(gitdir, common)
            config = os.path.join(os.path.realpath(base), "config")
            if os.path.isfile(config):
                return config
    config = os.path.join(gitdir, "config")
    return config if os.path.isfile(config) else None


def _gitdir(git_file, text):
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("gitdir:"):
            path = line[len("gitdir:"):].strip()
            if not path:
                return None
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(git_file), path)
            path = os.path.realpath(path)
            return path if os.path.isdir(path) else None
    return None


def _origin_url(text):
    section = None
    found = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != 'remote "origin"' or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "url":
            found = value.strip()
    return found


def _read_bounded(path):
    with open(path, "rb") as handle:
        data = handle.read(_MAX_CONFIG_BYTES + 1)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("git config is over the read bound")
    return data.decode("utf-8")
