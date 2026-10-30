"""Safe follow-on dispatch: a SECOND task to an existing, COMPLETED, healthy child Herdr.

`spawn_child` starts the herd before it dispatches, and `start_herd` refuses a live Supervisor unless `force` closes the
workspace: there was no path that REUSES a healthy herd, so the only alternative to refusing was destroying what must be
kept. This module is that path, and it is not a bypass:

  * it validates, BEFORE changing anything, the intended repository, the runtime, the task identity and every required role,
    and REFUSES naming the check that failed (an ACTIVE, errored, unreadable, missing, mismatched or unhealthy child is never
    taken over); an unreadable file is a refusal, never an empty default;
  * it never starts a herd, never closes a workspace, never resets a session or its context (`preserve_contexts`), never
    accepts `force`, and never edits the child's configuration;
  * the completed task's durable evidence is checked BEFORE the new task displaces `task.json`, and the new dependency is
    registered under its real task id BEFORE the dispatch, so a dispatch that fails after the child changed leaves a BLOCKING,
    recoverable record on the parent instead of an unregistered failed task (see `follow_on_dispatch`).

The registry (`children.json`) and what this module claims about it, exactly:

  * RECORDS: it appends ONE record of its own and later updates it, or (a rejected dispatch) removes it. It matches that record
    by full equality with what it appended, so it never edits, removes or replaces a record it did not create. A record it
    cannot find exactly once is a refusal, not a guess.
  * FILE: every write goes to a temporary file, is fsynced and `os.replace`d over the registry. A reader sees the OLD whole file
    or the NEW whole file, never a partly written one. This is NOT "an error leaves the old registry": once `os.replace` has
    returned, the new content is in place, and a later crash leaves it. A hard kill before the replace can leave a stray
    `children.json.tmp-<pid>` beside it (harmless: nothing reads it); an ordinary error removes it.
  * LOSS: the registry cannot testify to its own loss, so both writers also record a high-water mark of how many records each
    parent task has registered (`herdr.dependencies.ANCHOR_FILE`) and the completion gate compares the two: a registry that was
    deleted or lost records is an unresolved `REGISTRY_LOSS` entry, and the parent cannot complete over it. An absent registry that
    NOTHING contradicts is still the normal no-children state. This module raises the mark when it registers its intent (and
    withdraws the intent and refuses if it cannot), lowers it first on rollback, and refuses to build on a registry that already
    shows a loss. Residual: deleting BOTH the registry and its anchor is invisible to this directory (`UNPROTECTED`).
  * WRITERS: `registry_lock` is one exclusive lock on the registry. This module's operations hold it from the first read to the
    last write, and the EXISTING registry writer (`HerdrControlPlane.spawn_child`, via `_register_child`) takes the same lock
    around its own read-append-write, so those two cannot interleave. That is the protection; it is cooperative. A writer that
    does not take the lock is DETECTED, not excluded: each write re-reads the registry immediately before the replace and
    refuses if it is not the content the change was derived from. A change landing in the instant between that re-read and the
    replace is not caught; no mechanism available here (no database, a plain file) closes that instant.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import fcntl
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

from . import identity
from .dependencies import (
    child_dependencies,
    durable_complete_evidence,
    identities_of,
    missing_registrations,
    registration_identity,
    read_registration_anchor,
    register_identities,
    replace_registration,
    set_registration_anchor,
)
from .instance import HerdrInstance
from .lifecycle import logical_roles, role_counts
from .runtime import agent_info
from .tasks import dispatch_task

#: The registry lock is an exclusive `flock` on the parent's `.herd/state` DIRECTORY itself. No lock file is created, so taking it
#: writes nothing (a repository that has no state directory has nothing to serialize against and is refused, not populated).


class FollowOnRefused(RuntimeError):
    """A check failed (or the dispatch was rejected and rolled back): NOTHING durable was changed.

    `check` names the condition that failed, so the refusal says which.
    """

    def __init__(
        self,
        check: str,
        detail: str,
    ) -> None:
        super().__init__(
            f"Follow-on dispatch refused ({check}): {detail}"
        )

        self.check = check
        self.detail = detail


#: What a failed follow-on may truthfully say about whether the parent is blocked, ESTABLISHED BY ASKING THE GATE (never assumed):
#:   PENDING / FAILED      our record is in the registry in that state and the gate blocks on it
#:   RECOVERY              our record was LOST; a recovery record was written and the gate blocks on it
#:   REGISTRY_UNREADABLE   the registry cannot be read; the gate refuses an unreadable registry, so the parent cannot complete
#:                         until it is repaired; no record of this task can be confirmed
#:   UNPROTECTED           NOTHING is known to block the parent for this task (record lost and no recovery record could be
#:                         written): a person must act BEFORE the parent completes
#:   RESOLVED             our record is in the registry and the gate sees the task COMPLETE: nothing is unresolved
#:   REGISTRY_LOST        the registry lost records (or was deleted) and the gate blocks on that LOSS: the registration anchor
#:                         (herdr.dependencies.ANCHOR_FILE) says more were registered than the registry now holds. A person restores it.
#: UNPROTECTED is now reachable only when BOTH the registry and its anchor are gone (nothing in the state directory can then know).
STATES = ("PENDING", "FAILED", "RECOVERY", "REGISTRY_UNREADABLE", "REGISTRY_LOST", "UNPROTECTED", "RESOLVED")


class FollowOnFailed(RuntimeError):
    """The dispatch failed AFTER the child's state may have changed.

    `record_state` says what is true about the parent's protection, as established by asking the completion gate itself (see
    `STATES`). Only PENDING, FAILED and RECOVERY mean "a record of this task is in the registry and blocks the parent";
    REGISTRY_UNREADABLE means the gate refuses the unreadable registry; UNPROTECTED means nothing is known to block.
    """

    def __init__(
        self,
        detail: str,
        *,
        task_id: str,
        record_state: str,
    ) -> None:
        super().__init__(detail)

        self.task_id = task_id
        self.record_state = record_state


def _read_object(
    path: Path,
    check: str,
    what: str,
) -> tuple[dict, bytes]:
    """A JSON object from `path`, or a refusal that names the condition. Unreadable is never empty."""

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise FollowOnRefused(
            f"{check}_missing",
            f"{what} does not exist: {path}",
        )
    except OSError as exc:
        raise FollowOnRefused(
            f"{check}_unreadable",
            f"{what} cannot be read ({exc.__class__.__name__}): {path}",
        )

    try:
        data = json.loads(
            raw.decode("utf-8")
        )
    except Exception:
        raise FollowOnRefused(
            f"{check}_unreadable",
            f"{what} is not valid JSON: {path}",
        )

    if not isinstance(data, dict):
        raise FollowOnRefused(
            f"{check}_invalid",
            f"{what} is not a JSON object: {path}",
        )

    return data, raw


def _registry(
    parent: HerdrInstance,
) -> tuple[Path, dict, bytes]:
    path = (
        parent.herd_root
        / "state"
        / "children.json"
    )

    document, raw = _read_object(
        path,
        "registry",
        "The parent's child registry",
    )

    children = document.get(
        "children"
    )

    if not isinstance(
        children,
        list,
    ) or not all(
        isinstance(
            record,
            dict,
        )
        for record in children
    ):
        raise FollowOnRefused(
            "registry_invalid",
            "The parent's child registry has no `children` list of "
            f"records: {path}",
        )

    return path, document, raw


def _fsync_directory(
    path: Path,
) -> None:
    try:
        fd = os.open(
            path,
            os.O_RDONLY,
        )
    except OSError:
        return

    try:
        os.fsync(
            fd
        )
    except OSError:
        pass
    finally:
        os.close(
            fd
        )


def _write_registry(
    path: Path,
    document: dict,
    expected_sha256: str,
) -> None:
    """Replace the registry with `document`, which was derived from the bytes whose sha256 is `expected_sha256`.

    The new content goes to a temporary file in the same directory (fsynced); immediately before the replace the registry is read
    again and, if it is no longer those bytes, nothing is replaced and the write is refused (`registry_changed`): another writer
    changed it, and overwriting would discard what it wrote. The replace itself is atomic: a reader sees the old whole file or the
    new whole file. See the module docstring for what is and is not claimed.
    """

    data = (
        json.dumps(
            document,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    temporary = path.with_name(
        f"{path.name}.tmp-{os.getpid()}"
    )

    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(
            fd,
            "wb",
        ) as handle:
            handle.write(
                data
            )
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        try:
            os.chmod(
                temporary,
                stat.S_IMODE(
                    os.stat(
                        path
                    ).st_mode
                ),
            )
        except OSError:
            pass

        try:
            current = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            current = None

        if current != expected_sha256:
            raise FollowOnRefused(
                "registry_changed",
                "The parent's child registry changed after it was read; "
                "it was not overwritten.",
            )

        os.replace(
            temporary,
            path,
        )
    except BaseException:
        with contextlib.suppress(
            OSError
        ):
            os.unlink(
                temporary
            )

        raise

    _fsync_directory(
        path.parent
    )


@contextlib.contextmanager
def registry_lock(
    parent: HerdrInstance,
    *,
    wait_seconds: float = 0.0,
):
    """The registry's exclusive lock (an `flock` on the parent's state directory), held for a whole read-modify-write.

    Taken by every follow-on (for the whole operation, preflight to finalize) and by `HerdrControlPlane.spawn_child` around its own
    append. With `wait_seconds` 0 a busy lock is refused (`registry_busy`) immediately; otherwise it is retried until the time is
    up and then refused. Never queued without a bound.
    """

    path = (
        parent.herd_root
        / "state"
    )

    try:
        fd = os.open(
            path,
            os.O_RDONLY,
        )
    except OSError as exc:
        raise FollowOnRefused(
            "parent_not_initialized",
            f"The parent has no readable state directory ({exc.__class__.__name__}): {path}",
        )

    try:
        deadline = time.monotonic() + wait_seconds

        while True:
            try:
                fcntl.flock(
                    fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    # not contention: this filesystem or descriptor cannot be locked, and waiting will not change that
                    raise FollowOnRefused(
                        "registry_lock_unavailable",
                        f"The parent's state directory cannot be locked ({exc.__class__.__name__}: {exc.strerror}); nothing was "
                        "changed.",
                    )

                if time.monotonic() >= deadline:
                    raise FollowOnRefused(
                        "registry_busy",
                        "Another writer (a follow-on dispatch or a spawn) "
                        "holds the parent's child registry; nothing was "
                        "changed.",
                    )

                time.sleep(
                    0.05
                )

        yield
    finally:
        os.close(
            fd
        )


def normalize_task_policy(
    rules: list[str] | None,
    task_policy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The task-scoped policy for a follow-on: RULES ONLY.

    A follow-on adds task rules; it does not loosen anything. `git`, `review`, `scope` and every other key are refused, and the
    child's persisted configuration is never edited (rules ride on the task, not on `herd.config.json`).
    """

    merged: list[str] = []

    for source in (
        rules,
        (task_policy or {}).get(
            "rules"
        ),
    ):
        if source is None:
            continue

        if not isinstance(
            source,
            list,
        ) or not all(
            isinstance(
                rule,
                str,
            )
            and rule.strip()
            for rule in source
        ):
            raise ValueError(
                "Follow-on rules must be a list of non-empty strings."
            )

        for rule in source:
            rule = rule.strip()

            if rule not in merged:
                merged.append(
                    rule
                )

    if task_policy is not None:
        if not isinstance(
            task_policy,
            dict,
        ):
            raise ValueError(
                "`task_policy` must be an object."
            )

        extra = set(
            task_policy
        ) - {
            "rules"
        }

        if extra:
            raise ValueError(
                "A follow-on may add task rules only; refused "
                "policy key(s): "
                + ", ".join(
                    sorted(
                        extra
                    )
                )
                + "."
            )

    return {"rules": merged} if merged else None


def _required_roles(
    config: dict,
) -> list[str]:
    orchestration = config.get(
        "orchestration",
        {},
    )

    if not isinstance(
        orchestration,
        dict,
    ):
        raise FollowOnRefused(
            "child_config_invalid",
            "The child's `orchestration` is not an object.",
        )

    try:
        leads, pods = role_counts(
            orchestration
        )
    except (
        TypeError,
        ValueError,
    ):
        raise FollowOnRefused(
            "child_config_invalid",
            "The child's `leads`/`pods` are not integers.",
        )

    return logical_roles(
        leads,
        pods,
    )


#: How an identity verdict that is not PRESENT is named in a refusal. The verdicts are `herdr.identity`'s own; this only names them.
_IDENTITY_CHECKS = {
    identity.VERDICT_MISSING: "role_missing_live",
    identity.VERDICT_UNBOUND: "role_unbound",
    identity.VERDICT_REPLACED: "role_session_replaced",
    identity.VERDICT_DEGRADED: "role_identity_degraded",
}


def _inside(
    value: Any,
    repo: Path,
) -> bool:
    if not isinstance(
        value,
        str,
    ) or not value:
        return False

    try:
        path = Path(
            value
        ).expanduser().resolve()
    except (
        OSError,
        RuntimeError,
    ):
        return False

    return path == repo or repo in path.parents


def _verify_live_roles(
    child: HerdrInstance,
    config: dict,
    agents: dict,
    workspace_id: str,
) -> dict:
    """Every required role is the SAME LIVE SESSION this child bound, in THIS repository and workspace, and idle.

    "Idle" is a health check, not an identity check: a name can be reused and a workspace can move, so a live agent answering to
    `child-sup` proves only that something is alive under that name. Identity is `herdr.identity`'s, not a second system: the
    child's saved role bindings are compared with a fresh probe by `identity.classify`, and only PRESENT passes. REPLACED (the
    stable identity matches and the SESSION is a new one) is refused, because the follow-on exists to PRESERVE the sessions and their
    context; a role whose session was replaced satisfies "the right role is there" and defeats exactly that.

    Beyond the comparison, the binding itself must be this child's: it names the agent the runtime names, and its recorded `cwd` is
    inside the intended repository and its `workspace_id` is the runtime's. `foreground_cwd` and any configured model are never
    consulted (the first diverges from `cwd` in real herds and the second is configuration, not identity).

    Session ids are not copied into any refusal or record: only the verdict.
    """

    try:
        bindings = identity.load_bindings(
            child.herd_root
        )
    except identity.BindingsCorrupt:
        raise FollowOnRefused(
            "child_bindings_corrupt",
            "The child's saved role bindings cannot be read or are not a "
            "bindings document; without them no role can be shown to be "
            "the session the child bound.",
        )

    seen: dict[str, str] = {}

    for role in _required_roles(
        config
    ):
        name = agents.get(
            role
        )

        if (
            not isinstance(
                name,
                str,
            )
            or not name
        ):
            raise FollowOnRefused(
                "role_missing",
                f"The child has no `{role}` recorded.",
            )

        binding = bindings["roles"].get(
            role
        )

        probe = agent_info(
            name
        )

        verdict = identity.classify(
            role,
            binding,
            probe,
        )

        if verdict.verdict != identity.VERDICT_PRESENT:
            raise FollowOnRefused(
                _IDENTITY_CHECKS.get(
                    verdict.verdict,
                    "role_identity_degraded",
                ),
                f"`{role}` ({name}) is {verdict.verdict.upper()} "
                f"({verdict.problem}), not the session this child bound; "
                "the child's sessions would not be preserved.",
            )

        if binding.get("agent") != name:
            raise FollowOnRefused(
                "role_bound_to_other_agent",
                f"The child's binding for `{role}` names a different "
                f"agent than the runtime's `{name}`.",
            )

        stable = binding["stable"]

        if not isinstance(
            stable.get("cwd"),
            str,
        ) or not isinstance(
            stable.get("workspace_id"),
            str,
        ):
            raise FollowOnRefused(
                "role_binding_incomplete",
                f"The binding for `{role}` records no `cwd` or "
                "`workspace_id`, so it cannot be shown to belong to this "
                "repository and workspace.",
            )

        if not _inside(
            stable["cwd"],
            child.repo,
        ):
            raise FollowOnRefused(
                "role_not_in_child",
                f"`{role}` ({name}) is bound outside the intended "
                f"repository {child.repo}.",
            )

        if stable["workspace_id"] != workspace_id:
            raise FollowOnRefused(
                "role_workspace_mismatch",
                f"`{role}` ({name}) is bound to a workspace other than "
                "the child's runtime workspace.",
            )

        status = probe.get(
            "status"
        )

        if status not in identity.RESETTABLE_STATUSES:
            raise FollowOnRefused(
                "role_not_healthy",
                f"`{role}` ({name}) is {identity.safe_status(status)!r}, "
                "not idle; the child is not reused.",
            )

        seen[role] = identity.VERDICT_PRESENT

    return seen


def _preflight(
    parent: HerdrInstance,
    target: Path,
    expected_task_id: str | None,
) -> dict:
    """Every check, read-only. The first that fails refuses, by name; nothing has been changed when it does."""

    if not parent.initialized:
        raise FollowOnRefused(
            "parent_not_initialized",
            f"{parent.repo} is not an initialized Herdr.",
        )

    if target == parent.repo:
        raise FollowOnRefused(
            "target_is_parent",
            "A Herdr cannot follow on to itself.",
        )

    parent_state = (
        parent.herd_root
        / "state"
    )

    _read_object(
        parent_state / "runtime.json",
        "parent_runtime",
        "The parent's runtime state",
    )

    parent_task, _ = _read_object(
        parent_state / "task.json",
        "parent_task",
        "The parent's task state",
    )

    parent_task_id = parent_task.get(
        "id"
    )

    if (
        parent_task.get("status") != "ACTIVE"
        or not isinstance(
            parent_task_id,
            str,
        )
        or not parent_task_id
    ):
        raise FollowOnRefused(
            "parent_task_not_active",
            "A follow-on dependency attaches to an ACTIVE parent task "
            f"(the parent's task is {parent_task.get('status')!r}).",
        )

    registry_path, document, registry_raw = _registry(
        parent
    )

    # A registry that already shows LOSS (an identity the anchor says was registered is not in it) is not built upon: refuse, changing
    # nothing, and let a person restore it. An unreadable anchor is refused for the same reason the registry is.
    try:
        anchor_before = read_registration_anchor(
            parent
        ).get(
            parent_task_id,
            [],
        )
    except RuntimeError as exc:
        raise FollowOnRefused(
            "registry_anchor_unreadable",
            f"{exc}",
        )

    lost = missing_registrations(
        anchor_before,
        document["children"],
        parent_task_id,
    )

    if lost:
        raise FollowOnRefused(
            "registry_loss_detected",
            "The child registry no longer accounts for what was registered for the "
            f"parent's task ({'; '.join(lost[:3])}): records were lost or replaced. "
            "Restore it first.",
        )

    registered = []

    for record in document["children"]:
        repo_value = record.get(
            "repo"
        )

        if (
            record.get("parent_task_id") == parent_task_id
            and isinstance(
                repo_value,
                str,
            )
            and Path(
                repo_value
            ).expanduser().resolve() == target
        ):
            registered.append(
                record
            )

    for record in registered:
        if record.get("dispatch_state") == "PENDING":
            raise FollowOnRefused(
                "follow_on_pending",
                "A previous follow-on dispatch to this child was "
                "recorded and never finalized "
                f"(task {record.get('task_id')}); resolve it first.",
            )

    with_task = [
        record
        for record in registered
        if isinstance(
            record.get("task_id"),
            str,
        )
        and record["task_id"]
    ]

    if not with_task:
        raise FollowOnRefused(
            "child_not_registered",
            f"{target} is not registered as a child of the "
            f"parent's task {parent_task_id}.",
        )

    prior = with_task[-1]
    prior_id = prior["task_id"]

    if (
        expected_task_id is not None
        and expected_task_id != prior_id
    ):
        raise FollowOnRefused(
            "expected_task_mismatch",
            f"The parent recorded {prior_id} for this child, "
            f"not the expected {expected_task_id}.",
        )

    child = HerdrInstance(
        target
    )

    if not child.initialized:
        raise FollowOnRefused(
            "child_not_initialized",
            f"{target} is not an initialized Herdr.",
        )

    config, _ = _read_object(
        child.config_path,
        "child_config",
        "The child's configuration",
    )

    child_task, _ = _read_object(
        child.herd_root
        / "state"
        / "task.json",
        "child_task",
        "The child's task state",
    )

    status = child_task.get(
        "status"
    )

    if status == "ACTIVE":
        raise FollowOnRefused(
            "child_task_active",
            f"The child's task {child_task.get('id')} is ACTIVE; "
            "it is not taken over.",
        )

    if status != "COMPLETE":
        raise FollowOnRefused(
            "child_task_not_complete",
            f"The child's task {child_task.get('id')} is "
            f"{status!r}, not COMPLETE.",
        )

    if child_task.get("id") != prior_id:
        raise FollowOnRefused(
            "child_task_identity_mismatch",
            f"The child reports {child_task.get('id')}; the parent "
            f"recorded {prior_id}.",
        )

    evidence = durable_complete_evidence(
        child.repo,
        prior_id,
    )

    if not evidence["ok"]:
        raise FollowOnRefused(
            "prior_evidence_missing",
            f"No durable COMPLETE evidence for {prior_id}: "
            f"{evidence['reason']}.",
        )

    runtime, _ = _read_object(
        child.herd_root
        / "state"
        / "runtime.json",
        "child_runtime",
        "The child's runtime state",
    )

    runtime_repo = runtime.get(
        "repo"
    )

    if (
        not isinstance(
            runtime_repo,
            str,
        )
        or Path(
            runtime_repo
        ).expanduser().resolve() != child.repo
    ):
        raise FollowOnRefused(
            "child_runtime_repo_mismatch",
            "The child's runtime state does not name the intended "
            "repository.",
        )

    workspace_id = runtime.get(
        "workspace_id"
    )

    if (
        not isinstance(
            workspace_id,
            str,
        )
        or not workspace_id
    ):
        raise FollowOnRefused(
            "child_workspace_unproven",
            "The child's runtime state names no workspace.",
        )

    if (
        prior.get("workspace_id") is not None
        and prior["workspace_id"] != workspace_id
    ):
        raise FollowOnRefused(
            "runtime_workspace_changed",
            "The child's live workspace is not the one the parent "
            "recorded.",
        )

    agents = runtime.get(
        "agents"
    )

    if not isinstance(
        agents,
        dict,
    ):
        raise FollowOnRefused(
            "child_roles_unproven",
            "The child's runtime state names no agents.",
        )

    if (
        isinstance(
            prior.get("agents"),
            dict,
        )
        and prior["agents"] != agents
    ):
        raise FollowOnRefused(
            "runtime_agents_changed",
            "The child's live roles are not the ones the parent "
            "recorded.",
        )

    roles_seen = _verify_live_roles(
        child,
        config,
        agents,
        workspace_id,
    )

    return {
        "registry_path": registry_path,
        "registry": document,
        "registry_raw": registry_raw,
        "parent_task_id": parent_task_id,
        "prior": prior,
        "prior_id": prior_id,
        "evidence": evidence,
        "child": child,
        "runtime": runtime,
        "workspace_id": workspace_id,
        "agents": agents,
        "roles_seen": roles_seen,
        "anchor_before": anchor_before,
    }


def _protection(
    parent: HerdrInstance,
    parent_task_id: str,
    new_id: str,
    child_repo: Path,
) -> tuple[str, str | None]:
    """Ask the completion gate (not the file) whether it can see this task: ``('unreadable', None)`` | ``('absent', None)`` |
    ``('present', label)`` where a label is the entry's ``dispatch_state`` or, failing that, its current status; a present entry
    the gate resolves as COMPLETE has the label ``RESOLVED`` (it blocks nothing, because nothing is unresolved)."""

    try:
        found = child_dependencies(
            parent,
            parent_task_id,
        )
    except Exception:
        # the gate raises on an unreadable registry, and `herdctl task-complete` then refuses
        return "unreadable", None

    lost = any(
        entry.get("current_status") == "REGISTRY_LOSS"
        for entry in found
    )

    for entry in found:
        repo = entry.get(
            "repo"
        )

        if (
            entry.get("task_id") == new_id
            and isinstance(
                repo,
                str,
            )
            and Path(
                repo
            ).expanduser().resolve() == child_repo
        ):
            if entry.get(
                "current_status"
            ) == "COMPLETE":
                return "present", "RESOLVED"

            return "present", str(
                entry.get(
                    "dispatch_state"
                )
                or entry.get(
                    "current_status"
                )
                or "PENDING"
            )

    return ("lost" if lost else "absent"), None


def _establish_protection(
    parent: HerdrInstance,
    context: dict,
    appended: dict,
    registry_path: Path,
    reason: str,
    clock=time.time,
) -> tuple[str, str]:
    """After a failed edit of OUR record (or an unknown outcome): find out what really protects the parent, repair it if it is
    lost, and return ``(state, sentence)`` where the sentence says only what the gate showed.

    A record that is not in the registry cannot block anything, so "it stays PENDING" is a claim about a mechanism that may not
    exist. If the gate cannot see the task, a RECOVERY record (the same identity, ``dispatch_state: RECOVERY``) is appended and the
    gate is asked again; only if it sees it is RECOVERY reported.
    """

    new_id = appended["task_id"]
    child_repo = context["child"].repo
    parent_task_id = context["parent_task_id"]

    seen, detail = _protection(
        parent,
        parent_task_id,
        new_id,
        child_repo,
    )

    if seen == "present" and detail == "RESOLVED":
        return "RESOLVED", (
            "It is registered on the parent and the gate sees it COMPLETE: "
            "nothing is unresolved, so nothing needs to block."
        )

    if seen == "present":
        state = detail if detail in STATES else "PENDING"

        return state, (
            f"It is REGISTERED on the parent ({detail}) and blocks its "
            "completion until it is resolved."
        )

    if seen == "unreadable":
        return "REGISTRY_UNREADABLE", (
            "The parent's registry (or its registration anchor) cannot be read. "
            "The completion gate refuses an unreadable one, so the parent cannot "
            "complete until it is repaired; no record of this task can be confirmed."
        )

    problem = None

    try:
        document, raw = _read_object(
            registry_path,
            "registry",
            "The parent's child registry",
        )

        children = document.get(
            "children"
        )

        if not isinstance(
            children,
            list,
        ):
            raise FollowOnRefused(
                "registry_invalid",
                "The registry has no `children` list.",
            )

        children.append(
            dict(
                appended,
                dispatch_state="RECOVERY",
                task_status="UNRESOLVED",
                recovery_reason=reason[:500],
                recovered_at=int(
                    clock()
                ),
            )
        )

        _write_registry(
            registry_path,
            document,
            hashlib.sha256(
                raw
            ).hexdigest(),
        )

        register_identities(
            parent,
            parent_task_id,
            identities_of(
                children,
                parent_task_id,
            ),
        )
    except Exception as exc:
        problem = str(
            exc
        )

    seen, detail = _protection(
        parent,
        parent_task_id,
        new_id,
        child_repo,
    )

    if seen == "present":
        return "RECOVERY", (
            "Its registry record was LOST; a RECOVERY record was written and "
            "the completion gate blocks on it until a person resolves it."
        )

    if seen == "unreadable":
        return "REGISTRY_UNREADABLE", (
            "Its registry record was lost and the registry can no longer be "
            "read. The completion gate refuses an unreadable registry, so the "
            "parent cannot complete until it is repaired."
        )

    if seen == "lost":
        return "REGISTRY_LOST", (
            "The registry has LOST records (or was deleted): the registration "
            "anchor says more were registered than it now holds, and the "
            "completion gate blocks the parent on that loss until a person "
            f"restores the registry ({problem or 'a recovery record could not be added'})."
        )

    return "UNPROTECTED", (
        "NOTHING is known to block the parent's completion for this task: "
        "its registry record AND the registration anchor are both gone, and a "
        f"recovery record could not be written ({problem or 'the gate still cannot see it'}). "
        "A person must act BEFORE the parent completes."
    )


#: The statuses a task file holds. A status outside this set is not "not active": it is a state nothing writes, and unknown is not permission.
TASK_STATUSES = frozenset({"IDLE", "ACTIVE", "COMPLETE", "ABORTED", "ERROR"})


def parent_active_task_id(
    parent: HerdrInstance,
) -> str | None:
    """The id of the parent's ACTIVE task, or None when the parent has no active task. BEFORE `spawn_child` launches anything.

    Only two things mean "no dependency to register": the task file does not exist, or it plainly says the task is not active (IDLE,
    COMPLETE, ABORTED, ERROR). EVERYTHING ELSE REFUSES, by name, with nothing launched or written: a file that cannot be read, that is not a
    JSON object, that names a status nothing writes, or that is ACTIVE without a plain-string id. Unreadable is never empty.

    Why this is not a corner case: a heartbeat saves the task with `path.write_text(...)`, which opens the file for writing (so empties it)
    BEFORE it writes the new content, so a read in that window is a SUPPORTED transient, not an external act. Reading it as "no task" would
    register the child with no parent task id: orphaned, and skipped by completion once the parent's ACTIVE state returns. The follow-on
    already refuses these same conditions (`parent_task_*`); `spawn_child` was the writer that did not.
    """

    try:
        task, _ = _read_object(
            parent.herd_root
            / "state"
            / "task.json",
            "parent_task",
            "The parent's task state",
        )
    except FollowOnRefused as refusal:
        if refusal.check == "parent_task_missing":
            return None

        raise

    status = task.get(
        "status"
    )

    if status == "ACTIVE":
        task_id = task.get(
            "id"
        )

        if not isinstance(
            task_id,
            str,
        ) or not task_id:
            raise FollowOnRefused(
                "parent_task_invalid",
                "The parent's task state says ACTIVE but carries no plain task id, so a child cannot be registered under it.",
            )

        return task_id

    if status not in TASK_STATUSES:
        raise FollowOnRefused(
            "parent_task_invalid",
            f"The parent's task state names a status nothing writes ({status!r}); not read as \"no task\".",
        )

    return None


def admit_spawn_registration(
    parent: HerdrInstance,
    parent_task_id: str | None,
    target: Path,
) -> tuple | None:
    """BEFORE `spawn_child` launches a child, and under the registry lock: refuse to build on a registry that cannot be trusted, and make
    the registration durable BEFORE there is anything to register.

    Refuses (changing nothing, launching nothing) when the registry is unreadable or not a registry (it is never silently replaced by an
    empty one), when the anchor is unreadable, or when the registry no longer accounts for an identity the anchor says was registered (a
    loss is not built upon: an ordinary spawn cannot "restore the count" with replacement children). Otherwise it seeds the anchor from
    what the registry holds and RESERVES the target repository, so that a child launched and then never recorded is a loss that blocks,
    not a warning. Returns the reservation, or ``None`` when the parent has no active task (no dependency is registered).

    The reservation is NEVER withdrawn by the caller on an exception from the launch (see `HerdrControlPlane.spawn_child`): it is replaced by
    the child's identity once the record is written (`finalize_spawn_registration`) or cleared by a person.
    """

    path = (
        parent.herd_root
        / "state"
        / "children.json"
    )

    records: list = []

    if path.exists():
        document, _ = _read_object(
            path,
            "registry",
            "The parent's child registry",
        )

        children = document.get(
            "children"
        )

        if not isinstance(
            children,
            list,
        ) or not all(
            isinstance(
                entry,
                dict,
            )
            for entry in children
        ):
            raise FollowOnRefused(
                "registry_invalid",
                f"The parent's child registry has no `children` list of records: {path}",
            )

        records = children

    if parent_task_id is None:
        return None

    try:
        before = read_registration_anchor(
            parent
        ).get(
            parent_task_id,
            [],
        )
    except RuntimeError as exc:
        raise FollowOnRefused(
            "registry_anchor_unreadable",
            f"{exc}",
        )

    lost = missing_registrations(
        before,
        records,
        parent_task_id,
    )

    if lost:
        raise FollowOnRefused(
            "registry_loss_detected",
            "The child registry no longer accounts for what was registered for the "
            f"parent's task ({'; '.join(lost[:3])}): records were lost or replaced, so "
            "no child is launched on top of it. Restore it first.",
        )

    reservation = registration_identity(
        target,
        None,
    )

    try:
        register_identities(
            parent,
            parent_task_id,
            identities_of(
                records,
                parent_task_id,
            )
            + [reservation],
        )
    except Exception as exc:
        raise FollowOnRefused(
            "registry_anchor_unwritable",
            f"The registration anchor could not be written ({exc}); nothing was launched.",
        )

    return reservation


def finalize_spawn_registration(
    parent: HerdrInstance,
    parent_task_id: str,
    reservation: tuple,
    record: dict,
) -> str | None:
    """The record is written: swap the reservation for the child's concrete identity. Returns a problem or None. If this fails the
    reservation stays, and it is satisfied by the record's repository, so nothing is falsely blocked and nothing is unprotected."""

    mine = registration_identity(
        record.get(
            "repo"
        ),
        record.get(
            "task_id"
        ),
    )

    if mine is None or mine[1] is None:
        return None

    try:
        replace_registration(
            parent,
            parent_task_id,
            reservation,
            mine,
        )
    except Exception as exc:
        return str(
            exc
        )

    return None


def _new_task_id(
    text: str,
    taken: set[str],
) -> str:
    """The id `dispatch_task` would derive, fixed up front so the intent can be recorded under it."""

    new = (
        time.strftime("%Y%m%d-%H%M%S")
        + "-"
        + hashlib.sha1(
            text.encode()
        ).hexdigest()[:6]
    )

    if new in taken:
        raise FollowOnRefused(
            "task_id_collision",
            f"The new task id {new} is already recorded; retry.",
        )

    return new


def _edit_own_record(
    path: Path,
    appended: dict,
    edit,
) -> dict | None:
    """Apply `edit(children, index)` to the ONE record equal to `appended`, or refuse; write atomically.

    The match is full equality with the record this follow-on appended, so a record it did not create is never touched, whatever
    task id or repository it carries. Absent, or present more than once, is `registry_record_lost`: nothing is guessed.
    """

    document, raw = _read_object(
        path,
        "registry",
        "The parent's child registry",
    )

    children = document.get(
        "children"
    )

    if not isinstance(
        children,
        list,
    ):
        raise FollowOnRefused(
            "registry_invalid",
            "The parent's child registry lost its `children` list.",
        )

    ours = [
        index
        for index, entry in enumerate(
            children
        )
        if entry == appended
    ]

    if len(ours) != 1:
        raise FollowOnRefused(
            "registry_record_lost",
            f"The record for {appended.get('task_id')} is not in the "
            f"registry exactly once ({len(ours)} found); no record was "
            "changed.",
        )

    result = edit(
        children,
        ours[0],
    )

    _write_registry(
        path,
        document,
        hashlib.sha256(
            raw
        ).hexdigest(),
    )

    return result


def _update_own_record(
    path: Path,
    appended: dict,
    **fields,
) -> dict:
    def edit(
        children: list,
        index: int,
    ) -> dict:
        children[index] = dict(
            children[index],
            **fields,
        )

        return children[index]

    return _edit_own_record(
        path,
        appended,
        edit,
    )


def _remove_own_record(
    path: Path,
    appended: dict,
) -> None:
    def edit(
        children: list,
        index: int,
    ) -> None:
        del children[index]

    _edit_own_record(
        path,
        appended,
        edit,
    )


def _child_task_now(
    child: HerdrInstance,
) -> dict | None:
    try:
        data = json.loads(
            (
                child.herd_root
                / "state"
                / "task.json"
            ).read_text()
        )
    except Exception:
        return None

    return data if isinstance(
        data,
        dict,
    ) else None


def _observe_sessions(
    child: HerdrInstance,
    agents: dict,
    roles: list[str],
) -> dict[str, str]:
    """Each role's identity verdict RIGHT NOW (read-only, never raises). `unverified` is not a verdict: it is a failed look."""

    try:
        bindings = identity.load_bindings(
            child.herd_root
        )
    except Exception:
        return {role: "unverified" for role in roles}

    seen: dict[str, str] = {}

    for role in roles:
        try:
            seen[role] = identity.classify(
                role,
                bindings["roles"].get(
                    role
                ),
                agent_info(
                    agents[role]
                ),
            ).verdict
        except Exception:
            seen[role] = "unverified"

    return seen


def follow_on_dispatch(
    parent: HerdrInstance,
    target_repo: str | Path,
    *,
    task: str,
    rules: list[str] | None = None,
    task_policy: dict[str, Any] | None = None,
    expected_task_id: str | None = None,
    clock=time.time,
) -> dict:
    """Dispatch a SECOND task to the parent's existing, COMPLETED, healthy child, preserving its sessions and their context.

    Returns the dispatch result. Raises `FollowOnRefused` (nothing changed, or changed and rolled back) or `FollowOnFailed`
    (recoverable, registered, blocking the parent).

    The order is the safety:

      1. validate everything, read-only (the intended repository, the runtime, the task identity, the completed task's durable
         evidence, and every required role: the SAME live session the child bound, in this repository and workspace, idle);
      2. REGISTER the intent: append one record under the NEW task id, `dispatch_state: PENDING` (atomic). From here the parent
         cannot complete over this child, whatever happens next;
      3. dispatch with `preserve_contexts=True` (this path issues no context reset, no workspace close, no bootstrap);
      4. FINALIZE the record (atomic).

    A dispatch that fails is classified from what the child now says: if its task state is untouched (the first task still
    COMPLETE) the intent record is removed and the registry is as before (a REJECTION); if the child recorded the new task (it is
    ERROR, or ACTIVE with an unknown prompt outcome) the record stays, updated to what happened, and BLOCKS the parent
    (recoverable); if that cannot be told the record stays PENDING, which also blocks. There is no outcome in which the child has a
    failed task and the parent has no dependency on it. If the record itself cannot be updated or removed, it stays PENDING.

    The result says what was OBSERVED about sessions (`sessions.before` is what the preflight required, `sessions.after` a fresh
    look after the dispatch); it does not claim a preservation it did not look at.
    """

    task = (task or "").strip()

    if not task:
        raise ValueError(
            "Task description cannot be empty."
        )

    policy = normalize_task_policy(
        rules,
        task_policy,
    )

    target = Path(
        target_repo
    ).expanduser().resolve()

    with registry_lock(
        parent
    ):
        context = _preflight(
            parent,
            target,
            expected_task_id,
        )

        child = context["child"]
        registry_path = context["registry_path"]

        taken = {
            entry["task_id"]
            for entry in context["registry"]["children"]
            if isinstance(
                entry.get("task_id"),
                str,
            )
        }

        new_id = _new_task_id(
            task,
            taken,
        )

        before_sha = hashlib.sha256(
            context["registry_raw"]
        ).hexdigest()

        record = {
            "requested_at": int(
                clock()
            ),
            "parent_repo": str(
                parent.repo
            ),
            "parent_task_id": context["parent_task_id"],
            "dependency": True,
            "repo": str(
                child.repo
            ),
            "task_id": new_id,
            "task_status": "DISPATCHING",
            "workspace_id": context["workspace_id"],
            "agents": copy.deepcopy(
                context["agents"]
            ),
            "dispatch_state": "PENDING",
            "follow_on_of": context["prior_id"],
            "prior_evidence": {
                "task_id": context["prior_id"],
                "path": context["evidence"]["path"],
                "sha256": context["evidence"]["sha256"],
                "completed_at": context["evidence"]["completed_at"],
            },
        }

        document = copy.deepcopy(
            context["registry"]
        )

        document["children"].append(
            record
        )

        # Refused (nothing changed) if the registry is no longer the bytes this was derived from.
        _write_registry(
            registry_path,
            document,
            before_sha,
        )

        appended = copy.deepcopy(
            record
        )

        # Make the registration visible to the completion gate even if the registry is lost later. If that cannot be done the
        # intent is withdrawn and the dispatch is REFUSED here, before anything reached the child.
        try:
            register_identities(
                parent,
                context["parent_task_id"],
                identities_of(
                    document["children"],
                    context["parent_task_id"],
                ),
            )
        except Exception as anchoring:
            try:
                set_registration_anchor(
                    parent,
                    context["parent_task_id"],
                    context["anchor_before"],
                )
                _remove_own_record(
                    registry_path,
                    appended,
                )
            except Exception as undoing:
                state, detail = _establish_protection(
                    parent,
                    context,
                    appended,
                    registry_path,
                    f"anchor unwritable ({anchoring}) and the intent could not be withdrawn ({undoing})",
                    clock,
                )

                raise FollowOnFailed(
                    f"The registration anchor could not be written ({anchoring}) and the intent record for {new_id} "
                    f"could not be withdrawn ({undoing}); nothing was dispatched. {detail}",
                    task_id=new_id,
                    record_state=state,
                ) from anchoring

            raise FollowOnRefused(
                "registry_anchor_unwritable",
                f"The registration anchor could not be written ({anchoring}); the intent was withdrawn and nothing was dispatched.",
            ) from anchoring

        try:
            task_state = dispatch_task(
                child,
                task,
                task_policy=policy,
                preserve_contexts=True,
                task_id=new_id,
            )
        except Exception as exc:
            now = _child_task_now(
                child
            )

            if (
                now is not None
                and now.get("id") == context["prior_id"]
                and now.get("status") == "COMPLETE"
            ):
                # The child's task state is untouched: the dispatch was REJECTED before it began. Remove OUR record only.
                try:
                    set_registration_anchor(
                        parent,
                        context["parent_task_id"],
                        context["anchor_before"],
                    )
                    _remove_own_record(
                        registry_path,
                        appended,
                    )
                except Exception as removal:
                    seen, _ = _protection(
                        parent,
                        context["parent_task_id"],
                        new_id,
                        child.repo,
                    )

                    if seen == "absent":
                        # Nothing started (the child is untouched) and the intent record is already gone: there is nothing
                        # that needs to block, and the registry is as it should be.
                        raise FollowOnRefused(
                            "dispatch_rejected",
                            f"{exc}. The child was not changed and the "
                            "intent record is no longer in the registry "
                            "(something else removed it); nothing started.",
                        ) from exc

                    state, detail = _establish_protection(
                        parent,
                        context,
                        appended,
                        registry_path,
                        f"rollback failed: {removal}",
                        clock,
                    )

                    raise FollowOnFailed(
                        f"The dispatch was rejected ({exc}) and the child "
                        "is unchanged, but the intent record for "
                        f"{new_id} could not be removed ({removal}). "
                        f"{detail}",
                        task_id=new_id,
                        record_state=state,
                    ) from exc

                raise FollowOnRefused(
                    "dispatch_rejected",
                    f"{exc}. The child was not changed and the intent "
                    "record was removed.",
                ) from exc

            if (
                now is not None
                and now.get("id") == new_id
            ):
                state = str(
                    now.get(
                        "status",
                        "UNKNOWN",
                    )
                )

                try:
                    _update_own_record(
                        registry_path,
                        appended,
                        task_status=state,
                        dispatch_state="FAILED",
                        dispatch_error=str(
                            exc
                        )[:2000],
                    )

                    recorded = "FAILED"
                    detail = (
                        "It is REGISTERED on the parent (FAILED) and blocks "
                        "its completion until it is resolved."
                    )
                except Exception as marking:
                    recorded, detail = _establish_protection(
                        parent,
                        context,
                        appended,
                        registry_path,
                        f"could not mark FAILED: {marking}",
                        clock,
                    )

                raise FollowOnFailed(
                    f"The dispatch failed after the child recorded task "
                    f"{new_id} (status {state}): {exc}. {detail}",
                    task_id=new_id,
                    record_state=recorded,
                ) from exc

            recorded, detail = _establish_protection(
                parent,
                context,
                appended,
                registry_path,
                "the child's task state could not be confirmed",
                clock,
            )

            raise FollowOnFailed(
                f"The dispatch failed and the child's task state cannot "
                f"be confirmed ({exc}). {detail}",
                task_id=new_id,
                record_state=recorded,
            ) from exc

        try:
            final = _update_own_record(
                registry_path,
                appended,
                task_status=task_state.get(
                    "status"
                ),
                dispatch_state="DISPATCHED",
            )
        except Exception as exc:
            state, detail = _establish_protection(
                parent,
                context,
                appended,
                registry_path,
                f"could not finalize: {exc}",
                clock,
            )

            raise FollowOnFailed(
                f"Task {new_id} was dispatched but its record could not "
                f"be finalized ({exc}). {detail}",
                task_id=new_id,
                record_state=state,
            ) from exc

        after_raw = registry_path.read_bytes()

        return {
            "repo": str(
                child.repo
            ),
            "task": task_state,
            "child_record": final,
            "prior": record["prior_evidence"],
            "context_reset_issued": False,
            "workspace_close_issued": False,
            "sessions": {
                "before": context["roles_seen"],
                "after": _observe_sessions(
                    child,
                    context["agents"],
                    list(
                        context["roles_seen"]
                    ),
                ),
            },
            "registry": {
                "before_sha256": before_sha,
                "after_sha256": hashlib.sha256(
                    after_raw
                ).hexdigest(),
                "records": len(
                    json.loads(
                        after_raw
                    )["children"]
                ),
            },
        }
