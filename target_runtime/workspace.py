"""Managed target workspace lifecycle: materialize, verify, release.

A workspace materializes ONLY after exact one-shot approval
consumption (the Broker gates that; this module re-checks the lease
invariants). One leased directory per workflow id, under the
protected workspaces root OUTSIDE every repository (plan D-8), never
reused across workflows, released deterministically. Fail closed on
substitution, containment violations, dirty state, and crash
uncertainty (an unleased directory already present).
"""

import os
import secrets
import shutil

from telegram_operator.config import CONFIG_DIR_RELATIVE

from target_runtime.git_transport import GitTransportError

WORKSPACES_DIR_NAME = "workspaces"

PROBLEM_WORKSPACE_EXISTS = "workspace_directory_already_exists"
PROBLEM_WORKSPACE_ESCAPES = "workspace_escapes_containment"
PROBLEM_WORKSPACE_IN_CONTROL = "workspace_inside_control_repository"
PROBLEM_REMOTE_MISMATCH = "workspace_remote_identity_mismatch"
PROBLEM_BASELINE_MISMATCH = "workspace_baseline_mismatch"
PROBLEM_WORKSPACE_DIRTY = "workspace_dirty"
PROBLEM_GIT_FAILED = "workspace_git_operation_failed"
PROBLEM_LEASE_MISSING = "workspace_lease_missing"
PROBLEM_WORKSPACE_MISSING = "workspace_directory_missing"
PROBLEM_RELEASE_PATH_MISMATCH = "workspace_release_path_mismatch"


def default_workspaces_root(home=None):
    """The managed workspaces root, outside any repository."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(
        base, CONFIG_DIR_RELATIVE, WORKSPACES_DIR_NAME
    )


def _is_within(child_realpath, parent_realpath):
    if child_realpath == parent_realpath:
        return True
    return child_realpath.startswith(
        parent_realpath.rstrip(os.sep) + os.sep
    )


def lease_path(workspaces_root, workflow_id):
    return os.path.join(workspaces_root, workflow_id)


def _default_lease_id_factory():
    return "lease-" + secrets.token_hex(12)


def materialize(entry, transport, workspaces_root, now,
                lease_id_factory=None):
    """Clone and verify the exact approved target; lease it.

    Mutates ``entry`` (lease recorded) ONLY on full success; on any
    failure the partially-materialized directory is removed, the
    record is untouched, and ``(False, problem, detail)`` is
    returned. Verifies, in order: fresh un-reused directory,
    realpath containment under the workspaces root, containment
    outside the control repository, canonical remote identity, the
    approved baseline commit (checked out detached and re-read), and
    clean state.
    """
    workflow_id = entry["workflow_id"]
    canonical_url = entry["target"]["canonical_url"]
    baseline_sha = entry["approved_baseline"]["commit_sha"]
    control_realpath = entry["control_identity"]["repository_realpath"]
    os.makedirs(workspaces_root, mode=0o700, exist_ok=True)
    path = lease_path(workspaces_root, workflow_id)
    # Containment runs FIRST (round-08 finding B1): a recovered
    # record is adversarial input, so a traversing workflow id or a
    # mis-rooted workspaces directory must hit containment before any
    # other check can answer for it. (The record schema additionally
    # makes a traversing id unrepresentable; this is the independent
    # second layer.)
    real_root = os.path.realpath(workspaces_root)
    real_path = os.path.realpath(path)
    if not _is_within(real_path, real_root) or real_path == real_root:
        return False, PROBLEM_WORKSPACE_ESCAPES, (
            "lease path %s escapes the workspaces root %s"
            % (real_path, real_root)
        )
    if _is_within(real_path, os.path.realpath(control_realpath)):
        return False, PROBLEM_WORKSPACE_IN_CONTROL, (
            "lease path %s is inside the control repository"
            % real_path
        )
    if os.path.exists(path):
        # Crash uncertainty or reuse: a directory that exists before
        # this materialization is NEVER adopted.
        return False, PROBLEM_WORKSPACE_EXISTS, (
            "directory %s already exists; a pre-existing directory is"
            " never adopted (crash-ambiguous or reused)" % path
        )
    try:
        transport.clone(canonical_url, path)
        observed_remote = transport.remote_url(path)
        if observed_remote != canonical_url:
            raise GitTransportError(
                "remote identity %r is not the canonical %r"
                % (observed_remote, canonical_url)
            )
        transport.checkout_detached(path, baseline_sha)
        observed_head = transport.head_commit(path)
        if observed_head != baseline_sha:
            raise GitTransportError(
                "HEAD %r is not the approved baseline %r"
                % (observed_head, baseline_sha)
            )
        if transport.status_porcelain(path).strip():
            raise GitTransportError("workspace is dirty after clone")
    except GitTransportError as exc:
        shutil.rmtree(path, ignore_errors=True)
        detail = str(exc)
        if "remote identity" in detail:
            problem = PROBLEM_REMOTE_MISMATCH
        elif "baseline" in detail:
            problem = PROBLEM_BASELINE_MISMATCH
        elif "dirty" in detail:
            problem = PROBLEM_WORKSPACE_DIRTY
        else:
            problem = PROBLEM_GIT_FAILED
        return False, problem, detail
    make_lease_id = lease_id_factory or _default_lease_id_factory
    entry["workspace_lease"] = {
        "lease_id": make_lease_id(),
        "path_realpath": os.path.realpath(path),
        "acquired_at": now,
        "released_at": None,
    }
    return True, None, None


def verify_leased_workspace(entry, transport, workspaces_root):
    """Re-verify a leased workspace before ANY use (substitution).

    Read-only: containment, existence, canonical remote identity,
    and HEAD == approved baseline. A workspace swapped, moved, or
    re-pointed on disk after materialization fails closed here.
    """
    lease = entry["workspace_lease"]
    if not isinstance(lease, dict) or lease.get("released_at") is not (
        None
    ):
        return False, PROBLEM_LEASE_MISSING, (
            "no active workspace lease is recorded for %s"
            % entry["workflow_id"]
        )
    path = lease["path_realpath"]
    real_root = os.path.realpath(workspaces_root)
    if not _is_within(os.path.realpath(path), real_root):
        return False, PROBLEM_WORKSPACE_ESCAPES, (
            "leased path %s escapes the workspaces root %s"
            % (path, real_root)
        )
    expected_tail = os.path.join(real_root, entry["workflow_id"])
    if os.path.realpath(path) != expected_tail:
        return False, PROBLEM_WORKSPACE_ESCAPES, (
            "leased path %s is not this workflow's own directory %s"
            " (cross-workflow reuse)" % (path, expected_tail)
        )
    if not os.path.isdir(path):
        return False, PROBLEM_WORKSPACE_MISSING, (
            "leased workspace %s no longer exists" % path
        )
    try:
        observed_remote = transport.remote_url(path)
        if observed_remote != entry["target"]["canonical_url"]:
            return False, PROBLEM_REMOTE_MISMATCH, (
                "workspace remote %r is not the canonical %r"
                " (substituted workspace)"
                % (observed_remote, entry["target"]["canonical_url"])
            )
        observed_head = transport.head_commit(path)
        if observed_head != entry["approved_baseline"]["commit_sha"]:
            return False, PROBLEM_BASELINE_MISMATCH, (
                "workspace HEAD %r is not the approved baseline %r"
                " (substituted or advanced workspace)"
                % (
                    observed_head,
                    entry["approved_baseline"]["commit_sha"],
                )
            )
    except GitTransportError as exc:
        return False, PROBLEM_GIT_FAILED, str(exc)
    return True, None, None


def release(entry, workspaces_root, now):
    """Release the lease and remove the workspace directory.

    HARDENED (I3, finding 8 / criterion H): before ANY recursive
    removal, release independently requires the recorded lease
    identity AND that the recorded path is exactly THIS workflow's
    own ``lease_path(root, workflow_id)`` — the same check
    ``verify_leased_workspace`` makes. A record whose lease path
    names another workflow's directory (or anything else inside the
    managed root) is refused with its own problem code and the named
    directory is left byte-for-byte intact.
    """
    lease = entry["workspace_lease"]
    if not isinstance(lease, dict):
        return False, PROBLEM_LEASE_MISSING, "no lease recorded"
    if lease.get("released_at") is not None:
        return False, PROBLEM_LEASE_MISSING, (
            "the lease was already released at %r; release is not"
            " repeatable" % lease["released_at"]
        )
    lease_id = lease.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id:
        return False, PROBLEM_LEASE_MISSING, (
            "the lease carries no recorded lease identity; refusing"
            " to remove anything"
        )
    path = lease["path_realpath"]
    real_root = os.path.realpath(workspaces_root)
    expected = os.path.join(real_root, entry["workflow_id"])
    if os.path.realpath(path) != expected:
        return False, PROBLEM_RELEASE_PATH_MISMATCH, (
            "recorded lease path %s is not this workflow's own lease"
            " directory %s; a substituted or cross-workflow path is"
            " never removed" % (path, expected)
        )
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    lease["released_at"] = now
    return True, None, None
