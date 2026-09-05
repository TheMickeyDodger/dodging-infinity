"""Deterministic Git safety guards for Herdr repositories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from .runtime import run


HERD = ".herd"
CFG = "herd.config.json"
APPROVAL = "state/commit-approval.json"
PUSH_APPROVAL = "state/push-approval.json"


def hroot(repo: str | Path) -> Path:
    return Path(repo).resolve() / HERD


def gitout(
    repo: str | Path,
    *args: str,
    allow_fail: bool = False,
) -> str:
    result = run([
        "git",
        "-C",
        str(repo),
        *args,
    ])

    if result.returncode and not allow_fail:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
        )

    return result.stdout.strip()


def approval_path(
    repo: str | Path,
) -> Path:
    return hroot(repo) / APPROVAL


def push_approval_path(
    repo: str | Path,
) -> Path:
    return hroot(repo) / PUSH_APPROVAL


def repo_identity(
    repo: str | Path,
) -> dict:
    root = Path(
        gitout(
            repo,
            "rev-parse",
            "--show-toplevel",
        )
    ).resolve()

    branch = (
        gitout(
            root,
            "branch",
            "--show-current",
            allow_fail=True,
        )
        or "(detached HEAD)"
    )

    head = (
        gitout(
            root,
            "rev-parse",
            "HEAD",
            allow_fail=True,
        )
        or "(unborn)"
    )

    remote = (
        gitout(
            root,
            "remote",
            "get-url",
            "origin",
            allow_fail=True,
        )
        or "(no origin)"
    )

    gitdir = gitout(
        root,
        "rev-parse",
        "--git-dir",
    )

    if not Path(gitdir).is_absolute():
        gitdir = str(
            (root / gitdir).resolve()
        )

    staged = run([
        "git",
        "-C",
        str(root),
        "diff",
        "--cached",
        "--binary",
    ]).stdout.encode()

    return {
        "repo_root": str(root),
        "git_dir": gitdir,
        "branch": branch,
        "head": head,
        "remote": remote,
        "staged_sha256": hashlib.sha256(
            staged
        ).hexdigest(),
    }


def approval_valid(
    repo: str | Path,
    consume: bool = False,
):
    path = approval_path(repo)

    if not path.exists():
        return (
            False,
            "No approval exists. Run `herdctl approve-commit`.",
        )

    try:
        token = json.loads(
            path.read_text()
        )
    except Exception:
        return (
            False,
            "Approval token is unreadable. Re-authorize.",
        )

    if int(
        token.get("expires_at", 0)
    ) < int(time.time()):
        path.unlink(
            missing_ok=True
        )
        return (
            False,
            "Approval expired. Re-authorize.",
        )

    current = repo_identity(repo)

    for key in [
        "repo_root",
        "git_dir",
        "branch",
        "head",
        "staged_sha256",
    ]:
        if token.get(key) != current.get(key):
            path.unlink(
                missing_ok=True
            )
            return (
                False,
                f"Approval invalidated because "
                f"`{key}` changed. Re-authorize.",
            )

    if consume:
        path.unlink(
            missing_ok=True
        )

    return True, "approved"


def push_identity(
    repo: str | Path,
    remote_name: str = "origin",
    target_branch: str | None = None,
    target_tag: str | None = None,
) -> dict:
    root = Path(
        gitout(
            repo,
            "rev-parse",
            "--show-toplevel",
        )
    ).resolve()

    branch = (
        gitout(
            root,
            "branch",
            "--show-current",
            allow_fail=True,
        )
        or "(detached HEAD)"
    )

    head = (
        gitout(
            root,
            "rev-parse",
            "HEAD",
            allow_fail=True,
        )
        or "(unborn)"
    )

    if branch == "(detached HEAD)":
        raise RuntimeError(
            "Push approval requires a named local branch."
        )

    remote_url = gitout(
        root,
        "remote",
        "get-url",
        remote_name,
        allow_fail=True,
    )

    if not remote_url:
        raise RuntimeError(
            f"Remote `{remote_name}` not found."
        )

    if target_tag:
        source_ref = (
            f"refs/tags/{target_tag}"
        )
        source_oid = gitout(
            root,
            "rev-parse",
            source_ref,
            allow_fail=True,
        )
        if not source_oid:
            raise RuntimeError(
                f"Tag `{target_tag}` not found."
            )
        target_ref = source_ref
    else:
        target_branch = (
            target_branch
            or branch
        )
        source_ref = (
            f"refs/heads/{branch}"
        )
        source_oid = head
        target_ref = (
            f"refs/heads/{target_branch}"
        )

    return {
        "repo_root": str(root),
        "branch": branch,
        "head": head,
        "remote_name": remote_name,
        "remote_url": remote_url,
        "source_ref": source_ref,
        "source_oid": source_oid,
        "target_ref": target_ref,
    }


def push_approval_valid(
    repo: str | Path,
    remote_name: str | None = None,
    remote_url: str | None = None,
    updates=None,
    consume: bool = False,
):
    path = push_approval_path(
        repo
    )

    if not path.exists():
        return (
            False,
            "No push approval exists. Run `herdctl approve-push`.",
        )

    try:
        token = json.loads(
            path.read_text()
        )
    except Exception:
        return (
            False,
            "Push approval token is unreadable. Re-authorize.",
        )

    if int(
        token.get("expires_at", 0)
    ) < int(time.time()):
        path.unlink(
            missing_ok=True
        )
        return (
            False,
            "Push approval expired. Re-authorize.",
        )

    target_ref = str(
        token.get("target_ref")
        or ""
    )

    try:
        if target_ref.startswith(
            "refs/tags/"
        ):
            current = push_identity(
                repo,
                token.get(
                    "remote_name",
                    "origin",
                ),
                target_tag=target_ref.removeprefix(
                    "refs/tags/"
                ),
            )
        else:
            current = push_identity(
                repo,
                token.get(
                    "remote_name",
                    "origin",
                ),
                target_ref.removeprefix(
                    "refs/heads/"
                )
                or None,
            )
    except RuntimeError as exc:
        path.unlink(
            missing_ok=True
        )
        return False, str(exc)

    for key in [
        "repo_root",
        "branch",
        "head",
        "remote_name",
        "remote_url",
        "target_ref",
    ]:
        if token.get(key) != current.get(key):
            path.unlink(
                missing_ok=True
            )
            return (
                False,
                f"Push approval invalidated because "
                f"`{key}` changed. Re-authorize.",
            )

    if (
        remote_name is not None
        and token.get("remote_name")
        != remote_name
    ):
        return (
            False,
            "Push approval is for a different remote name.",
        )

    if (
        remote_url is not None
        and token.get("remote_url")
        != remote_url
    ):
        return (
            False,
            "Push approval is for a different remote URL.",
        )

    if updates is not None:
        if len(updates) != 1:
            return (
                False,
                "Push approval permits exactly one ref update.",
            )

        (
            local_ref,
            local_oid,
            remote_ref,
            _remote_oid,
        ) = updates[0]

        expected_local = (
            token.get("source_ref")
            or f"refs/heads/{token.get('branch')}"
        )
        expected_oid = (
            token.get("source_oid")
            or token.get("head")
        )

        if local_ref != expected_local:
            return (
                False,
                f"Push local ref `{local_ref}` "
                f"does not match approved "
                f"`{expected_local}`.",
            )

        if local_oid != expected_oid:
            return (
                False,
                "Push source changed after approval.",
            )

        if remote_ref != token.get(
            "target_ref"
        ):
            return (
                False,
                f"Push target `{remote_ref}` "
                f"does not match approved "
                f"`{token.get('target_ref')}`.",
            )

    if consume:
        path.unlink(
            missing_ok=True
        )

    return True, "approved"


def simple_git_commit(
    command: str,
):
    try:
        tokens = shlex.split(
            command,
            posix=True,
        )
    except Exception:
        return (
            False,
            "Could not safely parse command. "
            "Commit must be standalone `git commit ...`.",
        )

    if any(
        token in {
            "&&",
            "||",
            ";",
            "|",
            "&",
        }
        for token in tokens
    ):
        return (
            False,
            "Commit must be standalone, not chained "
            "with other shell operations.",
        )

    if (
        not tokens
        or Path(tokens[0]).name != "git"
    ):
        return False, ""

    if (
        "--no-verify" in tokens
        or "-n" in tokens
    ):
        return (
            False,
            "`--no-verify` is forbidden by the herd commit guard.",
        )

    if "-C" in tokens:
        return (
            False,
            "`git -C ... commit` is blocked. "
            "Commit from the confirmed worktree.",
        )

    if (
        len(tokens) < 2
        or tokens[1] != "commit"
    ):
        return False, ""

    return True, ""


def simple_git_push(
    command: str,
):
    try:
        tokens = shlex.split(
            command,
            posix=True,
        )
    except Exception:
        return (
            False,
            "Could not safely parse command. "
            "Push must be standalone `git push ...`.",
        )

    if any(
        token in {
            "&&",
            "||",
            ";",
            "|",
            "&",
        }
        for token in tokens
    ):
        return (
            False,
            "Push must be standalone, not chained "
            "with other shell operations.",
        )

    if (
        not tokens
        or Path(tokens[0]).name != "git"
    ):
        return False, ""

    if "-C" in tokens:
        return (
            False,
            "`git -C ... push` is blocked. "
            "Push from the confirmed worktree.",
        )

    if (
        len(tokens) < 2
        or tokens[1] != "push"
    ):
        return False, ""

    if (
        "--dry-run" in tokens
        or "-n" in tokens
    ):
        return True, "dry-run"

    if "--no-verify" in tokens:
        return (
            False,
            "`git push --no-verify` is forbidden "
            "by the herd push guard.",
        )

    if any(
        token in tokens
        for token in [
            "--force",
            "-f",
            "--force-with-lease",
            "--mirror",
            "--delete",
        ]
    ):
        return (
            False,
            "Destructive/force push flags are blocked "
            "by the herd push guard.",
        )

    return True, ""


def guard_pretool() -> int:
    try:
        data = json.load(
            sys.stdin
        )
    except Exception:
        return 0

    if data.get("tool_name") != "Bash":
        return 0

    command = (
        data.get("tool_input")
        or {}
    ).get(
        "command",
        "",
    )

    if "git" not in command:
        return 0

    cwd = Path(
        data.get("cwd")
        or os.getcwd()
    ).resolve()

    result = run([
        "git",
        "-C",
        str(cwd),
        "rev-parse",
        "--show-toplevel",
    ])

    repo = (
        Path(
            result.stdout.strip()
        ).resolve()
        if result.returncode == 0
        else None
    )

    if re.search(
        r"(?:^|[\s;&|])(?:/[^\s]+/)?git\s+[^\n]*\bcommit\b",
        command,
    ):
        ok, reason = simple_git_commit(
            command
        )

        if not ok:
            print(
                reason
                or (
                    "Commit blocked: use a standalone "
                    "`git commit ...` after approval."
                ),
                file=sys.stderr,
            )
            return 2

        if not repo:
            print(
                "Commit blocked: unable to identify repository.",
                file=sys.stderr,
            )
            return 2

        if not (
            hroot(repo)
            / CFG
        ).exists():
            print(
                f"Commit blocked: {repo} is not initialized "
                "for herd commit confirmation.",
                file=sys.stderr,
            )
            return 2

        valid, message = approval_valid(
            repo,
            consume=False,
        )

        if not valid:
            print(
                f"Commit blocked for {repo.name}: "
                f"{message}",
                file=sys.stderr,
            )
            return 2

    if re.search(
        r"(?:^|[\s;&|])(?:/[^\s]+/)?git\s+[^\n]*\bpush\b",
        command,
    ):
        ok, reason = simple_git_push(
            command
        )

        if not ok:
            print(
                reason
                or (
                    "Push blocked: use a standalone "
                    "`git push ...` after approval."
                ),
                file=sys.stderr,
            )
            return 2

        if reason == "dry-run":
            return 0

        if not repo:
            print(
                "Push blocked: unable to identify repository.",
                file=sys.stderr,
            )
            return 2

        if not (
            hroot(repo)
            / CFG
        ).exists():
            print(
                f"Push blocked: {repo} is not initialized "
                "for herd push confirmation.",
                file=sys.stderr,
            )
            return 2

        valid, message = push_approval_valid(
            repo,
            consume=False,
        )

        if not valid:
            print(
                f"Push blocked for {repo.name}: "
                f"{message}",
                file=sys.stderr,
            )
            return 2

    return 0


DELIVERY_STEP_BASE_REFRESH = "BASE_REFRESH"
DELIVERY_STEP_COMMIT = "COMMIT"
DELIVERY_STEP_PUSH = "PUSH"


def _delivery_receipt_decision(
    repo: str | Path,
    step: str,
    live: dict,
):
    """The SECOND guard-valid path: an exact, executing PR delivery
    receipt for this repository and step (P1-A6).

    Consulted only after the legacy token path has refused, and never
    widening it. The import is LAZY and every failure — import,
    store location, permissions, parse, ambiguity — is a refusal
    reason on this path only, so a git hook in any repository on this
    machine never pays the delivery package's import cost when no
    receipt is in play and never sees a traceback from it (Lead M1).
    """
    try:
        from pr_delivery import receipts as delivery_receipts

        return delivery_receipts.guard_decision(
            Path(repo).resolve(),
            step,
            live,
            time.time(),
        )
    except Exception as exc:  # never propagate out of a hook
        return (
            False,
            "delivery receipt path unavailable "
            f"({type(exc).__name__}: {str(exc)[:300]})",
        )


def _delivery_commit_live(
    repo: str | Path,
) -> dict:
    ident = repo_identity(repo)
    return {
        "repository_realpath": ident["repo_root"],
        "git_dir_realpath": ident["git_dir"],
        "branch": ident["branch"],
        "source_ref": f"refs/heads/{ident['branch']}",
        "head_before": ident["head"],
        "staged_sha256": ident["staged_sha256"],
    }


def guard_precommit(
    repo: str | Path,
) -> int:
    valid, message = approval_valid(
        repo,
        consume=False,
    )

    if not valid:
        receipt_ok, receipt_message = _delivery_receipt_decision(
            repo,
            DELIVERY_STEP_COMMIT,
            _delivery_commit_live(repo),
        )

        if not receipt_ok:
            print(
                f"HERD COMMIT BLOCKED: {message} "
                f"(delivery receipt: {receipt_message})",
                file=sys.stderr,
            )
            return 1

        print(
            f"HERD COMMIT PRE-CHECK AUTHORIZED BY {receipt_message}: "
            f"{Path(repo).resolve()}",
            file=sys.stderr,
        )
        return 0

    print(
        f"HERD COMMIT PRE-CHECK AUTHORIZED: "
        f"{Path(repo).resolve()}",
        file=sys.stderr,
    )

    return 0


def guard_reference_transaction(
    repo: str | Path,
    phase: str,
) -> int:
    updates = []

    for line in sys.stdin.read().splitlines():
        parts = line.split()

        if len(parts) >= 3:
            updates.append(
                (
                    parts[0],
                    parts[1],
                    parts[2],
                )
            )

    if phase == "committed":
        _consume_push_approval_on_transfer(
            repo,
            updates,
        )

    head_ref = gitout(
        repo,
        "symbolic-ref",
        "-q",
        "HEAD",
        allow_fail=True,
    )

    touches_head = bool(
        head_ref
        and any(
            ref == head_ref
            for _, _, ref in updates
        )
    )

    if not touches_head:
        return 0

    if phase == "prepared":
        valid, message = approval_valid(
            repo,
            consume=False,
        )

        if not valid:
            # Second path, in order: an executing COMMIT receipt bound
            # to the live identity, else an executing BASE_REFRESH
            # receipt bound to EXACTLY this (ref, old, new) update line.
            receipt_ok, receipt_message = _delivery_receipt_decision(
                repo,
                DELIVERY_STEP_COMMIT,
                _delivery_commit_live(repo),
            )

            if not receipt_ok:
                head_updates = [
                    (old, new)
                    for old, new, ref in updates
                    if ref == head_ref
                ]
                if len(head_updates) == 1:
                    old_oid, new_oid = head_updates[0]
                    receipt_ok, receipt_message = (
                        _delivery_receipt_decision(
                            repo,
                            DELIVERY_STEP_BASE_REFRESH,
                            {
                                "repository_realpath": str(
                                    Path(repo).resolve()
                                ),
                                "source_ref": head_ref,
                                "old_base_oid": old_oid,
                                "new_base_oid": new_oid,
                            },
                        )
                    )

            if not receipt_ok:
                print(
                    f"HERD HISTORY UPDATE BLOCKED: "
                    f"{message} "
                    f"(delivery receipt: {receipt_message})",
                    file=sys.stderr,
                )
                return 1

            return 0

        return 0

    if phase == "committed":
        approval_path(
            repo
        ).unlink(
            missing_ok=True
        )
        return 0

    return 0


def guard_prepush(
    repo: str | Path,
    remote_name: str,
    remote_url: str,
) -> int:
    updates = []

    for line in sys.stdin.read().splitlines():
        parts = line.split()

        if len(parts) >= 4:
            updates.append(
                (
                    parts[0],
                    parts[1],
                    parts[2],
                    parts[3],
                )
            )

    valid, message = push_approval_valid(
        repo,
        remote_name=remote_name,
        remote_url=remote_url,
        updates=updates,
        consume=False,
    )

    if not valid:
        receipt_ok, receipt_message = False, "no single ref update"

        # The receipt binds BOTH the configured remote URL and the
        # expanded push URL recorded at authorization. The hook is
        # handed the expanded push URL git will actually contact, so
        # it is compared against the BOUND value, never against another
        # live read: a url.<base>.insteadOf, pushInsteadOf or pushurl
        # added after authorization no longer matches (round-01 B2).
        configured_url = gitout(
            repo,
            "config",
            "--get",
            f"remote.{remote_name}.url",
            allow_fail=True,
        )

        if len(updates) == 1 and configured_url:
            local_ref, local_oid, remote_ref, remote_oid = updates[0]
            receipt_ok, receipt_message = _delivery_receipt_decision(
                repo,
                DELIVERY_STEP_PUSH,
                {
                    "repository_realpath": str(Path(repo).resolve()),
                    "remote_name": remote_name,
                    "remote_url_exact": configured_url,
                    "remote_url_push": remote_url,
                    "source_ref": local_ref,
                    "source_commit": local_oid,
                    "destination_ref": remote_ref,
                    "expected_remote_old_oid": remote_oid,
                },
            )
        elif len(updates) == 1:
            receipt_message = (
                f"{remote_name!r} is not a configured remote name"
            )

        if not receipt_ok:
            print(
                f"HERD PUSH BLOCKED: {message} "
                f"(delivery receipt: {receipt_message})",
                file=sys.stderr,
            )
            return 1

        print(
            f"HERD PUSH AUTHORIZED BY {receipt_message}: "
            f"{Path(repo).resolve()} -> {remote_name}",
            file=sys.stderr,
        )
        return 0

    # Do not consume here: git also runs pre-push for `git push --dry-run`
    # and gives this hook no way to tell a rehearsal from a real transfer.
    # _consume_push_approval_on_transfer (reference-transaction, committed
    # phase) consumes the token once the approved commit is observed on the
    # approved remote-tracking ref.

    print(
        f"HERD PUSH AUTHORIZED: "
        f"{Path(repo).resolve()} -> {remote_name}",
        file=sys.stderr,
    )

    return 0


def _consume_push_approval_on_transfer(
    repo: str | Path,
    updates,
) -> None:
    """Consume the push approval once the approved commit is observed on the
    approved remote-tracking ref, which is evidence that a transfer completed.

    `git push --dry-run` never updates the tracking ref, so it cannot consume.
    `git fetch` moves the tracking ref to the approved head only when that
    commit is already on the remote, so consuming is correct there as well.
    Unreadable or malformed tokens are consumed: fail closed.
    """
    path = push_approval_path(repo)

    if not path.exists():
        return

    try:
        token = json.loads(
            path.read_text()
        )
    except Exception:
        path.unlink(
            missing_ok=True
        )
        return

    remote_name = token.get("remote_name")
    branch = str(
        token.get("target_ref", "")
    ).removeprefix("refs/heads/")
    head = token.get("head")

    if not remote_name or not branch or not head:
        path.unlink(
            missing_ok=True
        )
        return

    tracking_ref = f"refs/remotes/{remote_name}/{branch}"

    for _old_oid, new_oid, ref in updates:
        if ref == tracking_ref and new_oid == head:
            path.unlink(
                missing_ok=True
            )
            return


def guard_cli_prefix() -> str:
    """Return a shell-safe package-owned guard command prefix."""

    package_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    return " ".join([
        "env",
        (
            "PYTHONPATH="
            + shlex.quote(
                str(package_root)
            )
        ),
        shlex.quote(
            sys.executable
        ),
        "-m",
        "herdr.guards",
    ])


def _install_one_git_hook(
    repo: str | Path,
    hook_name: str,
    marker: str,
    guard_line: str,
) -> None:
    repo = Path(
        repo
    ).resolve()

    hook_raw = gitout(
        repo,
        "rev-parse",
        "--git-path",
        f"hooks/{hook_name}",
    )

    hook = Path(
        hook_raw
    )

    if not hook.is_absolute():
        hook = (
            repo
            / hook
        ).resolve()

    hook.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        hook.exists()
        and marker
        in hook.read_text(
            errors="ignore"
        )
    ):
        return

    backup = hook.with_name(
        f"{hook_name}.pre-herd"
    )

    had_backup = False

    if hook.exists():
        if backup.exists():
            raise RuntimeError(
                "Cannot safely install commit guard: "
                f"both {hook} and {backup} exist."
            )

        hook.rename(
            backup
        )

        had_backup = True

    backup_call = (
        f'"{backup}" "$@"'
        if had_backup
        else ": # no previous hook"
    )

    hook.write_text(
        "#!/usr/bin/env bash\n"
        + marker
        + "\nset -e\n"
        + 'ROOT="$(git rev-parse --show-toplevel)"\n'
        + guard_line
        + "\n"
        + backup_call
        + "\n"
    )

    hook.chmod(
        0o755
    )


def _install_pre_push_hook(
    repo: str | Path,
) -> None:
    repo = Path(
        repo
    ).resolve()

    hook_raw = gitout(
        repo,
        "rev-parse",
        "--git-path",
        "hooks/pre-push",
    )

    hook = Path(
        hook_raw
    )

    if not hook.is_absolute():
        hook = (
            repo
            / hook
        ).resolve()

    hook.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    marker = (
        "# HERD PUSH GUARD v0.3"
    )

    if (
        hook.exists()
        and marker
        in hook.read_text(
            errors="ignore"
        )
    ):
        return

    backup = hook.with_name(
        "pre-push.pre-herd"
    )

    had_backup = False

    if hook.exists():
        if backup.exists():
            raise RuntimeError(
                "Cannot safely install push guard: "
                f"both {hook} and {backup} exist."
            )

        hook.rename(
            backup
        )

        had_backup = True

    backup_call = (
        f'"{backup}" "$@" < "$TMP"'
        if had_backup
        else ": # no previous hook"
    )

    prefix = guard_cli_prefix()

    script = (
        "#!/usr/bin/env bash\n"
        + marker
        + "\nset -e\n"
        + 'ROOT="$(git rev-parse --show-toplevel)"\n'
        + 'TMP="$(mktemp)"\n'
        + 'trap \'rm -f "$TMP"\' EXIT\n'
        + 'cat > "$TMP"\n'
        + prefix
        + ' prepush --repo-path "$ROOT"'
        + ' --remote-name "$1"'
        + ' --remote-url "$2"'
        + ' < "$TMP"\n'
        + backup_call
        + "\n"
    )

    hook.write_text(
        script
    )

    hook.chmod(
        0o755
    )


def install_git_guard(
    repo: str | Path,
) -> None:
    prefix = guard_cli_prefix()

    _install_one_git_hook(
        repo,
        "pre-commit",
        "# HERD COMMIT GUARD v0.3",
        (
            prefix
            + ' precommit --repo-path "$ROOT"'
        ),
    )

    _install_one_git_hook(
        repo,
        "reference-transaction",
        "# HERD REFERENCE GUARD v0.3",
        (
            prefix
            + ' reference --repo-path "$ROOT"'
            + ' --phase "$1"'
        ),
    )

    _install_pre_push_hook(
        repo
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="herdr-guards"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "pretool"
    )

    command = subparsers.add_parser(
        "precommit"
    )
    command.add_argument(
        "--repo-path",
        required=True,
    )

    command = subparsers.add_parser(
        "reference"
    )
    command.add_argument(
        "--repo-path",
        required=True,
    )
    command.add_argument(
        "--phase",
        required=True,
    )

    command = subparsers.add_parser(
        "prepush"
    )
    command.add_argument(
        "--repo-path",
        required=True,
    )
    command.add_argument(
        "--remote-name",
        required=True,
    )
    command.add_argument(
        "--remote-url",
        required=True,
    )

    args = parser.parse_args()

    try:
        if args.command == "pretool":
            code = guard_pretool()

        elif args.command == "precommit":
            code = guard_precommit(
                Path(
                    args.repo_path
                ).resolve()
            )

        elif args.command == "reference":
            code = guard_reference_transaction(
                Path(
                    args.repo_path
                ).resolve(),
                args.phase,
            )

        elif args.command == "prepush":
            code = guard_prepush(
                Path(
                    args.repo_path
                ).resolve(),
                args.remote_name,
                args.remote_url,
            )

        else:
            raise RuntimeError(
                f"Unknown guard command: {args.command}"
            )

    except RuntimeError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        code = 1

    raise SystemExit(
        code
    )


if __name__ == "__main__":
    main()
