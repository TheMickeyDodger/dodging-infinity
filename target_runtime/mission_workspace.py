"""Automatic Mission workspaces: ONE isolated, DI-prepared Git worktree
per approved Mission, so a dispatch never needs a path from the human.

Configuration, once: a local REPOSITORY (a checkout whose ``origin``
canonicalizes to the approved repository) and a WORKSPACES ROOT (an
existing, writable directory outside EVERY repository: no ancestor of it,
itself included, holds a ``.git`` entry or is a Git directory, which
covers the configured repository, its other worktrees, the control
repository, any third repository and bare repositories). Nothing here
reads the environment and no default path is assumed: an unconfigured or
unusable repository or root is a refusal (``resolve``), never a guess,
and the root is never created.

Derivation. The path is ``<root>/<mission_id>``
(``target_runtime.workspace.lease_path``). The Mission id is DI-minted,
durable and grammar-checked by Mission Core, so the same Mission always
derives the same path and two Missions never share one; nothing random
or clock-dependent enters it.

Ownership is PROVEN, never assumed. DI creates the worktree with ``git
worktree add --detach --lock --reason <marker>``: detached at the
recorded baseline, locked from its first moment (so ``git worktree
prune`` never removes it), and the lock reason is ``lock_marker``,
naming this Mission and the digest of its exact durable binding (Mission
id, revision, proposal digest, approved repository, configured
repository, path, baseline: ``mission.record.workspace_binding_digest``).
A worktree is EXACT (``inspect``) only when the path is a real directory
(no symlink), the configured repository records exactly one worktree
there, its lock reason is exactly the marker, its worktree identity is
RECIPROCAL (exactly one administrative directory of the configured
repository points back at this checkout, and the checkout's own ``.git``
pointer, a regular file, resolves to exactly that directory), its own Git
common directory is the configured repository's, it is detached at
exactly the baseline, it is clean, and it holds no Herdr state. Anything
else is a refusal naming what was seen, never a repair:

- an unrelated file or directory, empty or not: COLLISION;
- a checkout DI did not prepare for this binding (no marker or another
  one, a symlink, another repository's checkout, a ``.git`` pointer
  repointed at other metadata, or metadata that does not point back):
  FOREIGN;
- a ``.git`` or administrative ``gitdir`` pointer that is malformed (an
  embedded NUL, not UTF-8, not exactly one line, no target) or that cannot
  be read or resolved once opened: FOREIGN, a refusal and never an
  escaping exception, since ownership cannot be proven;
- DI's marker on another HEAD, or a recorded worktree that is gone:
  CONFLICT;
- uncommitted content: NOT CLEAN;
- Herdr state (``require_no_herdr_state``): ACTIVE, or CONFLICT for a
  stopped task's record alone.

Activity is not a Git fact. ``.herd`` is ignored by Git, so a clean
worktree can still hold a live Herdr task. A workspace about to receive a
NEW intent has, by construction, no task of this Mission yet, so ANY
Herdr task or runtime record in it belongs to work this Mission's intent
does not represent. It is read through the hardened state-artifact
primitive (``evidence.read_state_artifact``) and refused before anything
is recorded or started; the bridge repeats the check immediately before
the intent, the last step before the spawn.

Exclusivity and crashes (``prepare``). Creation runs under an exclusive,
NON-BLOCKING ``flock`` on one lock file in the root. The kernel releases
it when its holder exits, so a crash never leaves a stale lock; a
preparation that overlaps one in progress is refused BUSY, never queued
and never duplicated. Inside the lock the path is inspected again and a
worktree is added only when the path is absent. ``git worktree add``
removes what it began when it fails; a crash in the middle of one can
leave a partial directory, which the next inspection refuses (it is not
exact) rather than adopts or deletes.

Nothing here removes, moves, prunes, unlocks or cleans anything:
completed, paused, cancelled and blocked worktrees stay as evidence.
"""

import errno
import fcntl
import json
import os
import stat
from dataclasses import dataclass

from workflow_authority import canonical

from target_runtime import evidence as evidence_module
from target_runtime import prepare as prepare_module
from target_runtime.git_transport import CAPTURE_CAPTURED, GitTransportError
from target_runtime.workspace import lease_path

MARKER_PREFIX = "dodging-infinity mission workspace"
PREPARATION_LOCK_NAME = ".dodging-infinity-mission-workspaces.lock"

ABSENT = "absent"
EXACT = "exact"

PROBLEM_REPOSITORY_UNAVAILABLE = "mission_bridge_repository_unavailable"
PROBLEM_ROOT_UNAVAILABLE = "mission_bridge_workspace_root_unavailable"
PROBLEM_COLLISION = "mission_bridge_workspace_collision"
PROBLEM_FOREIGN = "mission_bridge_workspace_foreign"
PROBLEM_CONFLICT = "mission_bridge_workspace_conflict"
PROBLEM_NOT_CLEAN = "mission_bridge_workspace_not_clean"
PROBLEM_BUSY = "mission_bridge_workspace_busy"
PROBLEM_PREPARATION_FAILED = "mission_bridge_workspace_preparation_failed"
PROBLEM_ACTIVE = "mission_bridge_workspace_active"

# The Herdr records whose presence means a herd is, or was, at work here.
HERDR_STATE_DIRS = (".herd", "state")
HERDR_TASK_RECORD = "task.json"
HERDR_RUNTIME_RECORD = "runtime.json"
# The task statuses that mean a herd task stopped (``herdr.tasks``).
HERDR_STOPPED_STATUSES = ("COMPLETE", "ABORTED", "ERROR")
# The worktree pointer files: the checkout's ``.git`` names its
# administrative directory, whose ``gitdir`` names the checkout's ``.git``.
GITDIR_PREFIX = "gitdir: "


class WorkspaceRefusal(Exception):
    def __init__(self, problem, reason):
        super(WorkspaceRefusal, self).__init__(reason)
        self.problem = problem
        self.reason = reason


def _refuse(problem, reason):
    raise WorkspaceRefusal(problem, reason)


@dataclass(frozen=True)
class Configuration:
    """The configured repository and root, resolved and checked once."""

    repository_realpath: str
    common_dir: str
    root_realpath: str
    head_commit_sha: str


def workspace_path(workspaces_root, mission_id):
    """The Mission's own workspace path: a function of the root and the
    Mission id alone."""
    return lease_path(os.path.realpath(workspaces_root), mission_id)


def lock_marker(mission_id, binding_digest_sha256):
    """The lock reason DI writes on the worktree it prepares, and the only
    one it adopts."""
    return "%s %s binding %s" % (MARKER_PREFIX, mission_id,
                                 binding_digest_sha256)


def within(child_realpath, parent_realpath):
    return child_realpath == parent_realpath or child_realpath.startswith(
        parent_realpath.rstrip(os.sep) + os.sep)


def _looks_like_git_directory(path):
    """Git's own shape test for a repository directory (bare, or a
    ``.git``): a ``HEAD`` file beside ``objects`` and ``refs``."""
    return os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(
        os.path.join(path, "objects")) and os.path.isdir(
        os.path.join(path, "refs"))


def enclosing_repository(path_realpath):
    """The nearest directory, ``path_realpath`` itself included, that holds
    a ``.git`` entry of any kind or is a Git directory; None when there is
    none up to the filesystem root. A filesystem walk, not a Git query, so
    a broken pointer, a locale or a safety setting cannot make it miss."""
    current = path_realpath
    while True:
        if os.path.lexists(os.path.join(current, ".git")) or (
            _looks_like_git_directory(current)
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _full_commit(value):
    return isinstance(value, str) and len(value) == 40 and not (
        set(value) - set("0123456789abcdef"))


def resolve(repository, workspaces_root, approved_url, control_repo,
            transport):
    """The configured repository and root, or a refusal: the repository
    must be readable, its ``origin`` the approved repository and its HEAD
    a full commit; the root an existing, writable directory outside the
    repository and outside the control repository."""
    if repository is None:
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "automatic Mission workspaces are not configured: no local"
                " repository was given (configure a local checkout of %s)"
                % approved_url)
    if not isinstance(repository, str) or not os.path.isabs(repository):
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository must be an absolute path")
    real = os.path.realpath(repository)
    try:
        top = os.path.realpath(transport.toplevel(real))
        common = os.path.realpath(transport.common_dir(real))
        origin = transport.remote_url(real).strip()
        head = transport.head_commit(real).strip()
    except (GitTransportError, OSError) as exc:
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository %s is not a readable Git"
                " repository (%s)" % (real, exc))
    try:
        url = canonical.canonicalize_repository_url(origin).repository_url
    except canonical.CanonicalizationError as exc:
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository's origin %r is not a canonical"
                " repository URL (%s)" % (origin, exc))
    if url != approved_url:
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository %s is not the approved repository"
                " %s (its origin is %s); no workspace is prepared from"
                " another project" % (top, approved_url, url))
    if not _full_commit(head):
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository's HEAD is not a full commit id")
    if workspaces_root is None:
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "automatic Mission workspaces are not configured: no"
                " workspaces root was given")
    if not isinstance(workspaces_root, str) or not os.path.isabs(
        workspaces_root
    ):
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "the workspaces root must be an absolute path")
    root = os.path.realpath(workspaces_root)
    if not os.path.isdir(root):
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "the workspaces root %s is not an existing directory; it is"
                " never created implicitly" % root)
    for name, other in (("the configured repository", top),
                        ("the control repository",
                         os.path.realpath(control_repo))):
        if within(root, other):
            _refuse(PROBLEM_ROOT_UNAVAILABLE,
                    "the workspaces root %s is inside %s %s; Mission worktrees"
                    " live outside every repository" % (root, name, other))
    enclosing = enclosing_repository(root)
    if enclosing is not None:
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "the workspaces root %s is inside the repository at %s;"
                " Mission worktrees live outside every repository, and"
                " nothing was bound or created" % (root, enclosing))
    if not os.access(root, os.W_OK | os.X_OK):
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "the workspaces root %s is not writable" % root)
    return Configuration(top, common, root, head)


def _listed(text):
    """``git worktree list --porcelain -z``: each field ends with NUL and
    each record with an empty field."""
    records, current = [], {}
    for field in text.split("\0"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value if value else True
    if current:
        records.append(current)
    return records


# The post-open reads and the pointer resolution go through these module
# names so hermetic tests can inject the I/O failures they must refuse.
_fstat = os.fstat
_read = os.read
_realpath = os.path.realpath

CHECKOUT_POINTER = "the checkout's .git pointer"
ADMINISTRATIVE_POINTER = "the administrative directory's gitdir pointer"


def _unusable_pointer(role, path, why):
    _refuse(PROBLEM_FOREIGN,
            "%s %s %s; ownership is not provable, the worktree and its"
            " binding are kept, and nothing was repaired or started"
            % (role, path, why))


def _pointer_text(path, role):
    """The single line of one small pointer file, read without following a
    symlink at its final component, or None when the file cannot be opened
    or is not a regular file. Once it is open, a failure to inspect or read
    it is a refusal (``could not be read``), and so is content that cannot
    be one usable pointer line: longer than 4096 bytes, not UTF-8, an
    embedded NUL, or not exactly one non-empty line (``is malformed``).
    Nothing invalid ever reaches path resolution."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        try:
            if not stat.S_ISREG(_fstat(descriptor).st_mode):
                return None
            data = _read(descriptor, 4097)
        except OSError as exc:
            _unusable_pointer(role, path, "could not be read (%s)" % exc)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if len(data) > 4096:
        _unusable_pointer(role, path, "is malformed (longer than 4096 bytes)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _unusable_pointer(role, path, "is malformed (not UTF-8)")
    if "\x00" in text:
        _unusable_pointer(role, path, "is malformed (an embedded NUL byte)")
    line = text.strip()
    if not line or "\n" in line or "\r" in line:
        _unusable_pointer(role, path,
                          "is malformed (not exactly one non-empty line)")
    return line


def _pointer_target(base, value, role, path):
    """``value`` resolved against ``base``; any failure doing so is a
    refusal (``could not be resolved``)."""
    try:
        return _realpath(os.path.join(base, value))
    except (OSError, ValueError) as exc:
        _unusable_pointer(role, path, "could not be resolved (%s)" % exc)


def _admin_directories_naming(path, common_dir):
    """The configured repository's administrative directories whose
    ``gitdir`` points back at ``path``'s ``.git``. Any administrative
    pointer that is malformed or cannot be read or resolved is a refusal:
    whether it names this checkout cannot be proven."""
    worktrees = os.path.join(common_dir, "worktrees")
    try:
        names = sorted(os.listdir(worktrees))
    except OSError:
        return []
    expected = os.path.realpath(os.path.join(path, ".git"))
    found = []
    for name in names:
        admin = os.path.join(worktrees, name)
        pointer_path = os.path.join(admin, "gitdir")
        pointer = _pointer_text(pointer_path, ADMINISTRATIVE_POINTER)
        if pointer is not None and _pointer_target(
            admin, pointer, ADMINISTRATIVE_POINTER, pointer_path
        ) == expected:
            found.append(os.path.realpath(admin))
    return found


def _own_metadata(path):
    """The administrative directory the checkout's own ``.git`` pointer
    names, resolved; None when ``.git`` cannot be opened or is not a regular
    file. Content that is not one ``gitdir:`` line naming a target is a
    refusal (``is malformed``)."""
    pointer_path = os.path.join(path, ".git")
    pointer = _pointer_text(pointer_path, CHECKOUT_POINTER)
    if pointer is None:
        return None
    if not pointer.startswith(GITDIR_PREFIX) or not pointer[
        len(GITDIR_PREFIX):
    ].strip():
        _unusable_pointer(CHECKOUT_POINTER, pointer_path,
                          "is malformed (not one 'gitdir: TARGET' line)")
    return _pointer_target(path, pointer[len(GITDIR_PREFIX):], CHECKOUT_POINTER,
                           pointer_path)


def _registered(path, configuration, transport):
    try:
        text = transport.worktree_list(configuration.repository_realpath)
    except (GitTransportError, OSError) as exc:
        _refuse(PROBLEM_REPOSITORY_UNAVAILABLE,
                "the configured repository's worktrees could not be listed"
                " (%s)" % exc)
    return [r for r in _listed(text) if isinstance(r.get("worktree"), str)
            and os.path.realpath(r["worktree"]) == path]


def inspect(path, configuration, marker, baseline, transport):
    """``ABSENT`` or ``EXACT`` (see the module docstring), or a refusal.
    Read-only."""
    registered = _registered(path, configuration, transport)
    if not os.path.lexists(path):
        if registered:
            _refuse(PROBLEM_CONFLICT,
                    "%s is gone but the configured repository still records a"
                    " worktree there; a recorded worktree is never re-created"
                    " or pruned" % path)
        return ABSENT
    if os.path.islink(path):
        _refuse(PROBLEM_FOREIGN,
                "%s is a symbolic link: it was not prepared by DI, and its"
                " ownership is not provable" % path)
    if not os.path.isdir(path):
        _refuse(PROBLEM_COLLISION,
                "%s exists and is not a directory; an unrelated path is never"
                " adopted" % path)
    if not registered:
        if os.path.lexists(os.path.join(path, ".git")):
            _refuse(PROBLEM_FOREIGN,
                    "%s is a checkout the configured repository does not record"
                    " as its worktree: it was not prepared by DI, and it is"
                    " never adopted" % path)
        _refuse(PROBLEM_COLLISION,
                "%s exists and is not a worktree of the configured repository;"
                " an unrelated directory is never adopted" % path)
    if len(registered) != 1 or registered[0].get("locked") != marker:
        _refuse(PROBLEM_FOREIGN,
                "the worktree at %s was not prepared by DI for this Mission's"
                " binding (its lock reason is %r, not DI's marker); it is"
                " never adopted" % (path, registered[0].get("locked")))
    administrative = _admin_directories_naming(path, configuration.common_dir)
    own = _own_metadata(path)
    if len(administrative) != 1 or own != administrative[0]:
        _refuse(PROBLEM_FOREIGN,
                "the worktree at %s does not own its registration: its .git"
                " points at %s, while the administrative directory that points"
                " back at it is %s; a checkout whose metadata identity is not"
                " reciprocal is never adopted, and nothing is repaired"
                % (path, own, administrative or "none"))
    try:
        common = os.path.realpath(transport.common_dir(path))
    except (GitTransportError, OSError) as exc:
        _refuse(PROBLEM_FOREIGN,
                "the worktree at %s cannot name its own repository (%s); its"
                " ownership is not provable" % (path, exc))
    if common != configuration.common_dir:
        _refuse(PROBLEM_FOREIGN,
                "the worktree at %s belongs to the repository at %s, not the"
                " configured one; its ownership is not provable"
                % (path, common))
    entry = registered[0]
    if entry.get("detached") is not True or entry.get("HEAD") != baseline:
        _refuse(PROBLEM_CONFLICT,
                "the worktree at %s is not detached at the recorded baseline"
                " %s (it shows %s); a moved worktree is refused, never reset"
                % (path, baseline, entry.get("HEAD")))
    try:
        capture = transport.status_porcelain_readonly(path)
    except (GitTransportError, OSError) as exc:
        _refuse(PROBLEM_NOT_CLEAN,
                "the worktree status at %s could not be read (%s)" % (path, exc))
    if not isinstance(capture, dict) or capture.get("status") != (
        CAPTURE_CAPTURED
    ) or capture.get("text", "").strip():
        _refuse(PROBLEM_NOT_CLEAN,
                "the worktree at %s is not clean: uncommitted content would be"
                " indistinguishable from the run's own work, and it is kept"
                " exactly as it is" % path)
    require_no_herdr_state(path)
    return EXACT


def _herdr_record(path, name):
    status, _, _, text = evidence_module.read_state_artifact(
        path, HERDR_STATE_DIRS, name)
    if status == prepare_module.INSTRUCTION_ABSENT:
        return None, None
    if status != prepare_module.INSTRUCTION_READ or text is None:
        return status, None
    try:
        document = json.loads(text)
    except ValueError:
        return "unparsable", None
    return "read", document if isinstance(document, dict) else None


def require_no_herdr_state(path):
    """Refuse a workspace that holds any Herdr task or runtime record: ACTIVE
    unless its only record is a task that provably stopped (CONFLICT).
    Read-only, through the hardened primitive; an unreadable record is
    treated as activity, never as absence."""
    task_state, task = _herdr_record(path, HERDR_TASK_RECORD)
    runtime_state, _ = _herdr_record(path, HERDR_RUNTIME_RECORD)
    if task_state is None and runtime_state is None:
        return
    status = task.get("status") if isinstance(task, dict) else None
    named = "task %r (status %r)" % (
        task.get("id") if isinstance(task, dict) else None, status)
    if runtime_state is None and status in HERDR_STOPPED_STATUSES:
        _refuse(PROBLEM_CONFLICT,
                "the workspace %s holds the record of another Herdr %s, which"
                " this Mission's run intent does not represent; it is kept as"
                " it is and nothing is started over it" % (path, named))
    _refuse(PROBLEM_ACTIVE,
            "the workspace %s holds Herdr state this Mission's run intent does"
            " not represent (%s; runtime record %s): a herd may be at work"
            " there. .herd is ignored by Git, so a clean worktree does not"
            " prove it inactive; nothing was recorded or started"
            % (path, named if task_state is not None else "no task record",
               runtime_state or "absent"))


def prepare(path, configuration, marker, baseline, transport, create=True):
    """Create the worktree when the path is absent (only when ``create``),
    or adopt the exact one, under the root's exclusive lock; ``EXACT`` or a
    refusal. The lock is released when this returns, raises, or its
    process dies."""
    lock_path = os.path.join(configuration.root_realpath, PREPARATION_LOCK_NAME)
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                             0o600)
    except OSError as exc:
        _refuse(PROBLEM_ROOT_UNAVAILABLE,
                "the workspaces root's preparation lock %s could not be opened"
                " (%s)" % (lock_path, exc))
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                _refuse(PROBLEM_BUSY,
                        "another Mission workspace preparation holds the"
                        " workspaces root's lock; nothing was created, and a"
                        " retry inspects the path again")
            _refuse(PROBLEM_ROOT_UNAVAILABLE,
                    "the workspaces root's preparation lock could not be taken"
                    " (%s)" % exc)
        if inspect(path, configuration, marker, baseline, transport) == ABSENT:
            if not create:
                _refuse(PROBLEM_CONFLICT,
                        "nothing is prepared at %s to recover: the operator"
                        " recovery path never creates a workspace (dispatch"
                        " without a path prepares it)" % path)
            try:
                transport.add_worktree(configuration.repository_realpath, path,
                                       baseline, marker)
            except (GitTransportError, OSError) as exc:
                _refuse(PROBLEM_PREPARATION_FAILED,
                        "git did not confirm the worktree at %s (%s); the next"
                        " attempt inspects whatever is there and adopts only"
                        " an exact worktree" % (path, exc))
        return inspect(path, configuration, marker, baseline, transport)
    finally:
        os.close(descriptor)
