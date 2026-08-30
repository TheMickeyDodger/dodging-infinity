"""Workspace trust for the ONE managed workspace DI just materialized.

WHY THIS EXISTS
---------------
A Herdr the Runtime starts in a freshly materialized managed
workspace runs as an INTERACTIVE TTY session. The Claude CLI gates
the first interactive start in a directory not already recorded in
its configuration, behind the ``Quick safety check: Is this a project you created or one you
trust?`` dialog, which no unattended run can answer. The CLI exposes
no ``trust`` verb, no flag, and no settings key that grants it (see
the I1 evidence artifact for the executed derivation against
``claude 2.1.251``); its OWN diagnostics name exactly one alternative
to answering the dialog:

    "accept the trust dialog, or set
     projects[<path>].hasTrustDialogAccepted: true in <config>"

So DI writes that ONE key for that ONE path, and within this module
no other key or path is written.

THE SECURITY BOUNDARY
---------------------
This module is a security surface, not a convenience. Everything it
will and will not do is bounded by construction:

- It establishes trust ONLY for a path that is EXACTLY
  ``workspace.lease_path(workspaces_root, workflow_id)`` — this
  workflow's own lease directory under the DI-owned managed
  workspaces root. The boundary is DERIVED from
  ``target_runtime.workspace`` rather than retyped here, so within
  this module it does not drift from that definition; a change to
  ``lease_path`` itself moves this boundary with it. A other
  path — outside the root, inside the root but another workflow's,
  a symlink resolving elsewhere, a non-directory, a path that does
  not exist — is REFUSED with its own problem code, and within that
  refusal no byte of the file is written.
- It writes exactly one key, ``hasTrustDialogAccepted``, into exactly
  one ``projects`` entry. Within that write, no top-level (global)
  key is added, changed or removed, no other project entry is
  touched, and no ``allowedTools`` or other permission surface is
  widened — each byte-proven before and after. Outside that write,
  and disclosed: what another process does to the same file is not
  covered (see the lost-update residual below).
- It writes no ANCESTOR of the workspace. Two reasons, stated
  separately because they are not equally strong. First, and
  unconditionally: writing a directory DI did not materialize is a
  change to user-global state outside the blast radius this module
  claims, whatever the CLI then does with it. Second, as
  defence-in-depth: the CLI resolves trust by walking UP from the
  working directory, so an ancestor entry CAN confer trust on
  everything beneath it. That second reason does NOT bite today —
  the walk is bounded at the enclosing git root and a materialized
  workspace IS its own git root, which this increment's own Arm D
  demonstrated by executing it (an ancestor entry did not trust the
  workspace). It bites the moment a workspace is materialized as a
  SUBDIRECTORY of a repository, which is the standing constraint
  recorded for future increments. The refusals are kept for the
  first reason and armed for the second.
- A config this module fails to read, parse, or recognise is a
  REFUSAL: within this module, such a file is not repaired, not
  re-created and not rewritten from scratch — a file DI does not
  fully account for is a file DI does not write. Outside that, and
  disclosed: a file that parses but means something this module does
  not model is written, not refused.

CONCURRENCY — WHAT IS AND IS NOT GUARANTEED
-------------------------------------------
The config file is shared with each live Claude session on the
machine. Two separate properties:

1. NO TORN FILE, within one filesystem. The new content is written
   to a temp file in the SAME directory and moved into place with
   ``os.replace`` (an atomic rename). A concurrent reader observes
   either the whole old file or the whole new file rather than a
   partial one, and an interrupted write leaves the original
   untouched. Outside that boundary, and disclosed: a temp directory
   on a different filesystem would make the rename non-atomic, which
   is why the temp file is placed beside the target.
2. LOST UPDATES ARE BOUNDED, NOT ELIMINATED. The read-modify-write
   runs while holding the SAME cross-process lock the CLI itself
   takes: the directory ``<config>.lock``, created with an atomic
   ``mkdir`` (the ``proper-lockfile`` protocol the CLI runs under,
   ``stale`` = 10s). DI holds it for the duration of one small
   read-modify-write and does not heartbeat it, so a DI process that
   dies holding it is reclaimed by the CLI's own staleness rule
   rather than wedging the user's sessions. Within this module a lock
   held by another writer is not reclaimed or broken — sustained
   contention is a REFUSAL, not a forced write. Outside that, and
   disclosed below: a lock older than the CLI's own staleness window
   is reclaimed once, and two DI processes seeing the same stale lock
   have a small unguarded window.

   NOT covered: the CLI acquires this lock with ``retries: 0`` and,
   on contention, falls back to an UNLOCKED read-modify-write. A CLI
   write that begins in that fallback path can therefore still
   overwrite DI's entry. The post-write read-back below detects the
   case where DI's own key did not survive to disk; outside that
   moment a later clobber is not detected here. This is a disclosed
   residual, not a closed hole — its CONSEQUENCE is refused at the
   point of use, immediately before the spawn.

LIFETIME — THE GRANT IS REVOKED AT RELEASE (I5-1)
-------------------------------------------------
This module exposes ``revoke`` beside ``establish``, and
``TargetBroker._release`` calls it before removing the workspace
directory, so a released workflow no longer leaves its ``projects``
entry behind. The blast radius is bounded in WHAT is written (one
key, one entry, byte-proven) and now also in HOW LONG the grant
lasts.

Revocation reaches exactly one ``projects`` key: the one at this
workflow's own lease realpath. ``revoke`` deliberately does NOT
require the workspace directory to exist, while ``establish`` does. The reason is the condition this
closes: a crash between directory removal and entry removal leaves an entry
whose directory is gone, and within such a case a revocation that
demanded the directory would be unable to reach it. The checks that BIND the
key to this workflow — inside the managed root, equal to this
workflow's own derived lease path — are identical in both.

Outside revocation, and disclosed beside the guarantee: an entry
written before this module had revocation is not swept retroactively,
and an entry whose workflow record no longer exists has no owner able
to prove it, so it is left alone rather than removed on a name.

FAIL-CLOSED
-----------
Each failure returns ``(False, problem_code, detail)``. The caller
(``TargetBroker._materialize``) turns that into a DURABLE, terminal
BLOCKED workflow carrying a reason receipt, so within the phase
machine the workflow is not silently retried, does not fall back to
an interactive prompt, and does not reach dispatch.
"""

import errno
import hashlib
import json
import os
import secrets
import time
import unicodedata

from target_runtime.workspace import lease_path

# The CLI version every vendor-derived fact in this module was
# derived from. Round-01 H6: those facts (the trust key, the upward
# walk, `<config>.lock`, `retries: 0`, the 10s staleness window,
# `realpath: true`) are true OF THIS VERSION. The operator-facing
# documents name the same version, pinned to this constant, so a
# reader meets the boundary where they read the claim.
CLI_DERIVED_VERSION = "2.1.251"

CONFIG_FILE_NAME = ".claude.json"

# The exact key the CLI reads. Derived from the installed binary:
# `oe().projects?.[key]?.hasTrustDialogAccepted === true`.
TRUST_KEY = "hasTrustDialogAccepted"
PROJECTS_KEY = "projects"

# The lock the CLI itself takes around each global-config write:
# `${configPath}.lock`, created by mkdir, stale after 10s
# (proper-lockfile defaults observed in claude 2.1.251).
LOCK_SUFFIX = ".lock"
LOCK_STALE_SECONDS = 10.0
MAX_LOCK_ATTEMPTS = 100
LOCK_RETRY_SECONDS = 0.02

# The CLI writes the global config as `JSON.stringify(value, null, 2)`
# with no trailing newline and without escaping non-ASCII. These are
# the Python settings that reproduce those bytes exactly; a
# round-trip contract test pins that against the real file.
JSON_INDENT = 2
JSON_ENSURE_ASCII = False

PROBLEM_OUTSIDE_MANAGED_ROOT = "workspace_trust_path_outside_managed_root"
PROBLEM_NOT_OWN_LEASE = "workspace_trust_path_not_this_workflow_lease"
PROBLEM_LEASE_MISSING = "workspace_trust_lease_missing"
PROBLEM_TARGET_NOT_DIRECTORY = "workspace_trust_target_not_a_directory"
PROBLEM_CONFIG_MISSING = "workspace_trust_config_missing"
PROBLEM_CONFIG_UNREADABLE = "workspace_trust_config_unreadable"
PROBLEM_CONFIG_UNPARSABLE = "workspace_trust_config_unparsable"
PROBLEM_CONFIG_NOT_OBJECT = "workspace_trust_config_root_not_an_object"
PROBLEM_PROJECTS_MISSING = "workspace_trust_config_projects_missing"
PROBLEM_PROJECTS_NOT_OBJECT = "workspace_trust_config_projects_not_an_object"
PROBLEM_ENTRY_NOT_OBJECT = "workspace_trust_project_entry_not_an_object"
PROBLEM_CONFIG_LOCKED = "workspace_trust_config_lock_unavailable"
# C-2: a lock directory that does not be created for a reason that is NOT
# contention (permissions, a read-only filesystem, a missing parent).
# Reporting these as contention sends an operator hunting a competing
# writer that does not exist, which is not an actionable reason.
PROBLEM_LOCK_UNUSABLE = "workspace_trust_config_lock_directory_unusable"
PROBLEM_WRITE_FAILED = "workspace_trust_write_failed"
PROBLEM_READBACK_MISMATCH = "workspace_trust_readback_mismatch"
# Round-01 C-1: DI established trust in a configuration file that the
# Herdr this dispatch would start does not read (an injected
# `--config` run), so within that run no component consumes the
# establishment.
PROBLEM_CONFIG_NOT_CONSUMED = "workspace_trust_config_not_the_one_consumed"
# Round-01 H4: trust was established, but is absent at the POINT OF
# USE — a concurrent CLI writer dropped it between establishment and
# dispatch. Refusing here converts the one known fail-open into a
# durable, actionable block.
PROBLEM_TRUST_NOT_PRESENT = "workspace_trust_absent_at_point_of_use"

# Each refusal this module can return. Consumers reference this
# tuple rather than retyping a literal.
TRUST_PROBLEM_CODES = (
    PROBLEM_OUTSIDE_MANAGED_ROOT,
    PROBLEM_NOT_OWN_LEASE,
    PROBLEM_LEASE_MISSING,
    PROBLEM_TARGET_NOT_DIRECTORY,
    PROBLEM_CONFIG_MISSING,
    PROBLEM_CONFIG_UNREADABLE,
    PROBLEM_CONFIG_UNPARSABLE,
    PROBLEM_CONFIG_NOT_OBJECT,
    PROBLEM_PROJECTS_MISSING,
    PROBLEM_PROJECTS_NOT_OBJECT,
    PROBLEM_ENTRY_NOT_OBJECT,
    PROBLEM_CONFIG_LOCKED,
    PROBLEM_LOCK_UNUSABLE,
    PROBLEM_WRITE_FAILED,
    PROBLEM_READBACK_MISMATCH,
    PROBLEM_CONFIG_NOT_CONSUMED,
    PROBLEM_TRUST_NOT_PRESENT,
)


# The scoped stop-reason marker for a failed establishment. Scoped,
# like the existing `vblock-`/`rblock-` markers — NOT the deferred
# general refusal-record mechanism. It carries the problem code and
# no content else: no path, no lease id, no capability.
TRUST_BLOCK_RECEIPT_MARKER = "workspace trust not established"


def trust_block_receipt(problem, now, turn_id_factory=None):
    """The durable, actionable E-5 receipt for a refused
    establishment. Capability-free and path-free: within the receipt
    the problem code is the only actionable content it carries, and
    the digest is
    that code's own sha256, so it is self-describing without carrying
    a secret."""
    make_turn_id = turn_id_factory or (
        lambda: "tblock-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(problem.encode("utf-8")).hexdigest(),
        "bounded_summary": "%s: %s" % (
            TRUST_BLOCK_RECEIPT_MARKER, problem
        ),
    }


def default_config_path(home=None):
    """The user-global Claude config the CLI reads."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, CONFIG_FILE_NAME)


def resolve_config_path(config_path):
    """The REAL path of the configuration file.

    Round-01 finding B3. ``~/.claude.json`` is very often a symlink
    into a dotfiles repository. Everything this module does — the
    lock, the temp file's directory, the atomic rename, and the
    read-back — must act on the RESOLVED path, or:

    - ``os.replace`` onto the unresolved path REPLACES THE SYMLINK
      with a regular file, detaching the file the user actually
      maintains and leaving it stale. That is a change to the
      identity of the user's global config, which is far outside
      "one key in one entry";
    - the read-back then re-reads the link path, finds the new
      regular file, and returns a FALSE GREEN;
    - and DI would lock ``<link>.lock`` while the CLI locks
      ``<realpath>.lock`` (the CLI passes ``realpath: true``), so the
      two processes would take DIFFERENT locks and exclude nobody —
      exactly the case where the lock matters.

    Resolving (rather than refusing a symlinked config) is the chosen
    position: it is what the CLI itself does, it keeps the user's
    symlink intact, and it makes the shared-lock guarantee true
    instead of narrowing the supported configurations.
    """
    return os.path.realpath(config_path)


def trust_key(path):
    """The config key the CLI looks a directory up under.

    Derived from the installed binary's own
    ``getWorkspacePersistedTrustKey``: the resolved real path,
    Unicode-normalized to NFC. (The CLI canonicalizes to the
    enclosing git root; a materialized workspace IS its own git root
    — ``workspace.materialize`` clones directly into the lease
    directory — so the two coincide for each path this module will
    accept.)
    """
    return unicodedata.normalize("NFC", os.path.realpath(path))


def is_trusted(config_path, path):
    """Read-only: does the config record trust for ``path``?

    Mirrors the CLI's own direct lookup (strict ``is True``). Within
    this reader, an unreadable / unparsable / wrong-shaped config
    resolves to NOT trusted — the fail-closed direction. Outside it,
    and disclosed: this answers for the file at the moment it is
    read, and not for its later state.
    """
    try:
        with open(config_path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    projects = document.get(PROJECTS_KEY)
    if not isinstance(projects, dict):
        return False
    entry = projects.get(trust_key(path))
    if not isinstance(entry, dict):
        return False
    return entry.get(TRUST_KEY) is True


def _is_within(child_realpath, parent_realpath):
    if child_realpath == parent_realpath:
        return True
    return child_realpath.startswith(
        parent_realpath.rstrip(os.sep) + os.sep
    )


def _acquire_lock(lock_path, sleeper=None, clock=None):
    """The CLI's own lock protocol: an atomic ``mkdir``.

    Returns ``(acquired, problem, detail)``. Within this function, a
    lock another writer holds is not reclaimed, broken or removed — a lock still held after
    the bounded wait is a refusal. A lock older than the CLI's own
    staleness window is abandoned by definition (the CLI heartbeats
    its lock each 5s; this module does not heartbeat), so within
    that window exactly that case is reclaimed once — see the C-3
    residual below for what that does not cover.

    C-2, within this function: an ``mkdir`` that fails for a reason
    other than the
    directory already existing — no permission, a read-only
    filesystem, a missing parent — is reported with its own problem
    code rather than as contention, because "look for the competing
    writer" is not an actionable instruction when there is none.

    C-3, disclosed window: two DI processes that observe the SAME
    stale lock can each reclaim it once, and the second ``rmdir`` can
    remove the first's freshly created lock. The ``reclaimed`` flag
    bounds this to a single attempt per call, and the consequence is
    one lost mutual-exclusion window on a file whose lost-update
    residual is already disclosed above. It is not closed here.
    """
    sleep = sleeper or time.sleep
    now = clock or time.time
    reclaimed = False
    for _ in range(MAX_LOCK_ATTEMPTS):
        try:
            os.mkdir(lock_path, 0o700)
            return True, None, None
        except FileExistsError:
            pass
        except OSError as exc:
            return False, PROBLEM_LOCK_UNUSABLE, (
                "the lock directory %s could not be created (%s: %s);"
                " this is not contention — no competing writer is"
                " implied" % (
                    lock_path, errno.errorcode.get(
                        exc.errno, exc.errno
                    ), exc.strerror or exc,
                )
            )
        if not reclaimed:
            try:
                age = now() - os.stat(lock_path).st_mtime
            except OSError:
                age = None
            if age is not None and age > LOCK_STALE_SECONDS:
                reclaimed = True
                try:
                    os.rmdir(lock_path)
                except OSError:
                    pass
                continue
        sleep(LOCK_RETRY_SECONDS)
    return False, PROBLEM_CONFIG_LOCKED, (
        "another writer holds %s; within this module a lock held by"
        " another writer is not reclaimed or broken — a lock older"
        " than the staleness window is the one exception" % lock_path
    )


def _release_lock(lock_path):
    try:
        os.rmdir(lock_path)
    except OSError:
        pass


def _atomic_write(config_path, payload, mode):
    """Temp file in the SAME directory + ``os.replace``.

    Same filesystem, so the rename is atomic: a concurrent reader
    sees the whole old file or the whole new one, and an interrupted
    write leaves the original byte-for-byte intact.
    """
    directory = os.path.dirname(config_path) or "."
    temp_path = os.path.join(
        directory,
        ".%s.di-trust-%s" % (
            os.path.basename(config_path), secrets.token_hex(8)
        ),
    )
    try:
        handle = os.open(
            temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, config_path)
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return True


def _read_document(config_path):
    """(document, problem, detail). Within this reader no content is
    repaired: a config it fails to parse is returned as a refusal,
    not rewritten."""
    try:
        with open(config_path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None, PROBLEM_CONFIG_MISSING, (
            "the Claude configuration %s does not exist; DI never"
            " creates it" % config_path
        )
    except OSError as exc:
        return None, PROBLEM_CONFIG_UNREADABLE, (
            "the Claude configuration %s could not be read: %s"
            % (config_path, exc)
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, PROBLEM_CONFIG_UNPARSABLE, (
            "the Claude configuration %s is not parsable JSON (%s);"
            " it is never rewritten from scratch" % (config_path, exc)
        )
    if not isinstance(document, dict):
        return None, PROBLEM_CONFIG_NOT_OBJECT, (
            "the Claude configuration %s has a %s at its root, not an"
            " object" % (config_path, type(document).__name__)
        )
    return document, None, None


def resolve_managed_target(entry, workspaces_root,
                           require_directory=True):
    """The one path establishment may target, or a refusal.

    Returns ``(real_path, problem, detail)``. Within this function
    the accepted path is EXACTLY
    ``lease_path(workspaces_root, workflow_id)`` resolved — derived
    from ``target_runtime.workspace`` rather than retyped — and each
    other path is refused.

    ``require_directory`` is True for establishment, which targets a
    workspace that must exist. I5-1's REVOCATION passes False, and the
    reason is the case revocation exists to clean: a crash between
    directory removal and entry removal leaves the entry behind with
    no directory, and within such a case a revocation that demanded the
    directory would be unable to reach it. The path checks that BIND the
    target to this workflow — inside the managed root, and equal to
    this workflow's own derived lease path — are unchanged in both
    modes, so relaxing the existence check does not widen WHICH key
    may be touched.
    """
    workflow_id = entry["workflow_id"]
    lease = entry.get("workspace_lease")
    if not isinstance(lease, dict) or not isinstance(
        lease.get("path_realpath"), str
    ) or not lease["path_realpath"]:
        return None, PROBLEM_LEASE_MISSING, (
            "no materialized workspace lease is recorded for %s"
            % workflow_id
        )
    recorded = lease["path_realpath"]
    real_root = os.path.realpath(workspaces_root)
    real_expected = os.path.realpath(
        lease_path(workspaces_root, workflow_id)
    )
    real_target = os.path.realpath(recorded)
    if not _is_within(real_target, real_root) or (
        real_target == real_root
    ):
        return None, PROBLEM_OUTSIDE_MANAGED_ROOT, (
            "%s resolves outside the DI-managed workspaces root %s;"
            " trust is never established outside it"
            % (real_target, real_root)
        )
    if real_target != real_expected:
        return None, PROBLEM_NOT_OWN_LEASE, (
            "%s is not workflow %s's own lease directory %s; trust is"
            " never established for another workflow's workspace"
            % (real_target, workflow_id, real_expected)
        )
    if require_directory and not os.path.isdir(real_target):
        return None, PROBLEM_TARGET_NOT_DIRECTORY, (
            "%s is not an existing directory" % real_target
        )
    return real_target, None, None


def establish(entry, workspaces_root, config_path, sleeper=None,
              clock=None):
    """Record trust for THIS workflow's own materialized workspace.

    Returns ``(True, None, None)`` only when a fresh read of the
    config from disk shows the intended state. Each other outcome is
    ``(False, problem, detail)`` with the file left byte-unchanged
    (or, for a read-back mismatch, with the discrepancy reported
    rather than retried).
    """
    real_target, problem, detail = resolve_managed_target(
        entry, workspaces_root
    )
    if problem is not None:
        return False, problem, detail
    key = trust_key(real_target)

    # B3: each path operation below acts on the RESOLVED config, so a
    # symlinked ~/.claude.json keeps its identity and DI takes the
    # same lock the CLI does.
    config_path = resolve_config_path(config_path)
    lock_path = config_path + LOCK_SUFFIX
    acquired, problem, detail = _acquire_lock(
        lock_path, sleeper=sleeper, clock=clock
    )
    if not acquired:
        return False, problem, detail
    try:
        document, problem, detail = _read_document(config_path)
        if problem is not None:
            return False, problem, detail
        if PROJECTS_KEY not in document:
            return False, PROBLEM_PROJECTS_MISSING, (
                "the Claude configuration %s has no %r object; DI"
                " never creates one" % (config_path, PROJECTS_KEY)
            )
        projects = document[PROJECTS_KEY]
        if not isinstance(projects, dict):
            return False, PROBLEM_PROJECTS_NOT_OBJECT, (
                "the Claude configuration %s has a %s at %r, not an"
                " object" % (
                    config_path, type(projects).__name__, PROJECTS_KEY
                )
            )
        existing = projects.get(key)
        if existing is not None and not isinstance(existing, dict):
            return False, PROBLEM_ENTRY_NOT_OBJECT, (
                "the existing entry for %s is a %s, not an object; it"
                " is never replaced" % (key, type(existing).__name__)
            )
        if isinstance(existing, dict) and existing.get(
            TRUST_KEY
        ) is True:
            # Idempotent: already exactly the intended state, so the
            # minimal write is NO write at all.
            return True, None, None
        try:
            mode = os.stat(config_path).st_mode & 0o777
        except OSError as exc:
            return False, PROBLEM_CONFIG_UNREADABLE, (
                "could not stat %s: %s" % (config_path, exc)
            )
        # The ONLY mutation: one key, in one entry. Each other
        # project entry and every top-level key is carried through
        # untouched, in place.
        updated = dict(existing) if isinstance(existing, dict) else {}
        updated[TRUST_KEY] = True
        projects[key] = updated
        payload = json.dumps(
            document, indent=JSON_INDENT,
            ensure_ascii=JSON_ENSURE_ASCII,
        ).encode("utf-8")
        try:
            _atomic_write(config_path, payload, mode)
        except OSError as exc:
            return False, PROBLEM_WRITE_FAILED, (
                "could not write %s atomically: %s"
                % (config_path, exc)
            )
        # POST-WRITE READ-BACK: re-read from disk rather than trust
        # the in-memory document. A mismatch is a refusal.
        if not is_trusted(config_path, real_target):
            return False, PROBLEM_READBACK_MISMATCH, (
                "after writing, a fresh read of %s does not record"
                " trust for %s" % (config_path, key)
            )
    finally:
        _release_lock(lock_path)
    return True, None, None


# --------------------------------------------------------------------
# I5-1: REVOCATION
# --------------------------------------------------------------------

#: The revocation half of the fixed-marker receipt vocabulary. Kept
#: distinct from TRUST_BLOCK_RECEIPT_MARKER so a reader of the durable
#: record can tell a refused ESTABLISHMENT from a refused REVOCATION.
TRUST_REVOKE_RECEIPT_MARKER = "workspace trust not revoked"

PROBLEM_REVOKE_READBACK = "workspace_trust_revoke_readback_mismatch"


def revoke_block_receipt(problem, now, turn_id_factory=None):
    """The durable, actionable receipt for a refused revocation.

    Same shape as `trust_block_receipt`: capability-free, path-free,
    and digested over the problem code, so within the receipt the code
    is the only actionable content it carries.
    """
    make_turn_id = turn_id_factory or (
        lambda: "trevoke-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": hashlib.sha256(problem.encode("utf-8")).hexdigest(),
        "bounded_summary": "%s: %s" % (
            TRUST_REVOKE_RECEIPT_MARKER, problem
        ),
    }


def revoke(entry, workspaces_root, config_path, sleeper=None,
           clock=None):
    """Remove THIS workflow's own trust entry, and only that entry.

    The mirror of `establish`, under the same discipline, and the
    close of the LIFETIME residual disclosed above: the grant stops
    being permanent.

    What may be removed, and the boundary in the same breath: exactly
    the `projects` key equal to `trust_key(real_target)`, where
    `real_target` is this workflow's OWN derived lease path inside the
    managed root — so another project's entry, an entry at a path this
    workflow has not leased, and every top-level key sit outside the
    reach of this function. It removes the whole entry rather
    than only the trust flag, because DI created the entry: leaving a
    de-trusted husk would accumulate exactly what the residual
    complained about.

    Returns `(True, None, None)` only when a fresh read from disk
    shows the key gone. Each other outcome is
    `(False, problem, detail)`, and a config this function refuses is
    left byte-unchanged.

    Idempotent: an entry already absent is success, since the intended
    state is what is asserted rather than the act of writing. That
    matters for restart — a crash between the write and the caller's
    own durable record leaves a second call correct rather than a
    failure.
    """
    real_target, problem, detail = resolve_managed_target(
        entry, workspaces_root, require_directory=False
    )
    if problem is not None:
        return False, problem, detail
    key = trust_key(real_target)

    config_path = resolve_config_path(config_path)
    lock_path = config_path + LOCK_SUFFIX
    acquired, problem, detail = _acquire_lock(
        lock_path, sleeper=sleeper, clock=clock
    )
    if not acquired:
        return False, problem, detail
    try:
        document, problem, detail = _read_document(config_path)
        if problem is not None:
            # A corrupt or unreadable configuration is a REFUSAL, not
            # an opportunity to rewrite it: the same posture
            # establishment takes.
            return False, problem, detail
        if PROJECTS_KEY not in document:
            return False, PROBLEM_PROJECTS_MISSING, (
                "the Claude configuration %s has no %r object; DI"
                " never creates one" % (config_path, PROJECTS_KEY)
            )
        projects = document[PROJECTS_KEY]
        if not isinstance(projects, dict):
            return False, PROBLEM_PROJECTS_NOT_OBJECT, (
                "the Claude configuration %s has a %s at %r, not an"
                " object" % (
                    config_path, type(projects).__name__, PROJECTS_KEY
                )
            )
        if key not in projects:
            # Already in the intended state: the minimal write is no
            # write at all.
            return True, None, None
        try:
            mode = os.stat(config_path).st_mode & 0o777
        except OSError as exc:
            return False, PROBLEM_CONFIG_UNREADABLE, (
                "could not stat %s: %s" % (config_path, exc)
            )
        # THE ONLY MUTATION: one key removed from `projects`. Every
        # sibling entry and every top-level key is carried through in
        # place, which `tests/test_ownership.py` proves by comparing
        # the surrounding bytes rather than by reading this comment.
        del projects[key]
        payload = json.dumps(
            document, indent=JSON_INDENT,
            ensure_ascii=JSON_ENSURE_ASCII,
        ).encode("utf-8")
        try:
            _atomic_write(config_path, payload, mode)
        except OSError as exc:
            return False, PROBLEM_WRITE_FAILED, (
                "could not write %s atomically: %s"
                % (config_path, exc)
            )
        # POST-WRITE READ-BACK from disk, not from the in-memory
        # document.
        fresh, problem, detail = _read_document(config_path)
        if problem is not None:
            return False, problem, detail
        fresh_projects = fresh.get(PROJECTS_KEY)
        if not isinstance(fresh_projects, dict) or key in fresh_projects:
            return False, PROBLEM_REVOKE_READBACK, (
                "after writing, a fresh read of %s still records an"
                " entry for %s" % (config_path, key)
            )
    finally:
        _release_lock(lock_path)
    return True, None, None
