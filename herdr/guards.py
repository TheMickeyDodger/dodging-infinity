"""Deterministic Git safety guards for Herdr repositories.

What these guards are, and are not. They check an agent's Bash tool calls
through the PreToolUse hook (``guard_pretool``), and Git's own hooks in a
repository where they are installed. These gates are workflow guardrails;
they are not designed to contain processes running with the user's own
privileges (see ``docs/operations.md``).

- Commit, push and MERGE each need their own human approval, and no kind
  ever stands in for another. A merge approval (``herdctl approve-merge
  --source REV``) authorizes exactly ONE local merge of the source commit it
  names into the branch and HEAD it was granted for. Each layer checks only
  what it can establish where it runs:

  - the pretool check sees the command: one ``git merge`` source, resolved
    now, must be the approved commit, into the approved destination. It
    judges the repository of the payload's working directory, so a merge
    must run there as ONE standalone ``git merge ...``: any repository
    redirection (``git -C``, ``--git-dir``, ``--work-tree``, any global
    option before ``merge``, an environment prefix, ``cd`` or another chained
    command, a nested shell, shell substitution) is refused, as ``git -C``
    already is for commit and push. Variables already exported in the
    agent's shell are not visible to it;
  - ``pre-merge-commit`` cannot see the source for a fresh automatic merge
    (in upstream Git's ``builtin/merge.c`` the hook runs before
    ``MERGE_HEAD`` is written: source-based inference, not a test of the
    installed Git), so it checks only that an approval exists for this
    destination;
  - ``pre-commit``, which a CONFLICTED merge completed by ``git commit``
    runs instead, checks ``MERGE_HEAD`` against the approved source;
  - ``reference-transaction`` sees the actual update, for every path: the
    branch may move only from the approved HEAD, to the approved source by a
    TRUE fast-forward (the approved HEAD an ancestor of it, ``git merge-base
    --is-ancestor``) or to a commit whose parents are exactly the approved
    HEAD and the approved source. Any other update it judges RETIRES the
    approval (a correctly shaped retry then needs a fresh one), and it
    consumes the approval when the approved update is committed.

  Where a merge is IDENTIFIABLE (``MERGE_HEAD`` present at ``pre-commit``; at
  the ref update, a commit with two or more parents, or a fast-forward of
  more than one commit), the merge approval is required FIRST and
  independently: a valid commit approval never stands in for it. Ordinary
  commits keep the commit gate unchanged. A fast-forward by exactly one
  commit whose parent is the old HEAD looks the same as a commit at the ref
  level and is judged as one.

  Commands with NO approval kind here are refused outright, never unlocked
  by a merge approval: ``gh pr create`` (opening a pull request is its own
  delivery action), ``gh pr merge`` (its base cannot be re-checked locally
  where it is used), ``git pull`` (what it merges is unknown before it
  fetches), and, as conservative OVER-REFUSAL, any ``gh api`` call naming
  pulls or merges, reads included.
- The approval ledger (``state/approval-ledger.jsonl``) is TAMPER-EVIDENCE
  against NON-ADVERSARIAL change (an accident, a crashed write, an unrelated
  tool), not a control: once ``herdctl`` has minted into it, an approval
  record that was edited, reused, written by something else, or deleted
  while still outstanding is refused, named as tamper evidence, instead of
  silently treated as absent and re-mintable. Like the gates, it is not
  designed to contain processes running with the user's own privileges.
  Every read-and-append of it, every mint (record and entry) and every
  retirement (read, removal and entry) is ONE operation under
  ``ledger_lock`` (a blocking ``flock``),
  so legitimate writers never break the chain themselves. A human mint
  that finds it broken archives a copy and atomically publishes a new
  chain; the broken one stays in force until then, so an interrupted
  recovery still refuses. That is what "Re-authorize" means here.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import threading
import time
from pathlib import Path

from .runtime import run


HERD = ".herd"
CFG = "herd.config.json"
APPROVAL = "state/commit-approval.json"
PUSH_APPROVAL = "state/push-approval.json"
MERGE_APPROVAL = "state/merge-approval.json"
LEDGER = "state/approval-ledger.jsonl"
LEDGER_LOCK = "state/approval-ledger.lock"
LEDGER_GENESIS = "0" * 64
KIND_COMMIT = "commit"
KIND_PUSH = "push"
KIND_MERGE = "merge"
MERGE_IDENTITY_KEYS = ("repo_root", "git_dir", "branch", "head")
MERGE_OPERATION_LOCAL = "local_merge"


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


def merge_approval_path(
    repo: str | Path,
) -> Path:
    return hroot(repo) / MERGE_APPROVAL


# -- typed human confirmation ----------------------------------------------


def _controlling_terminal():
    """The controlling terminal, opened for reading and writing; raises
    OSError when the process has none. Module-level so tests substitute it."""
    return open("/dev/tty", "r+", encoding="utf-8")


def typed_confirmation(prompt: str, expected: str) -> bool:
    """Whether the human typed ``expected`` at the CONTROLLING TERMINAL.

    Never read from stdin, and there is no flag that skips it: piping the
    answer into a command, or calling it from a tool with no terminal,
    confirms nothing. It is a workflow guardrail, not designed to contain
    processes running with the user's own privileges, and not proof that a
    human typed the answer.
    """
    try:
        terminal = _controlling_terminal()
    except OSError:
        return False
    with terminal:
        terminal.write(prompt)
        terminal.flush()
        answer = terminal.readline()
    return answer.strip() == expected


# -- the approval ledger: TAMPER-EVIDENCE, not a control -------------------


def ledger_path(
    repo: str | Path,
) -> Path:
    return hroot(repo) / LEDGER


# One critical section per ledger: process-local bookkeeping that makes it
# re-entrant (a retirement inside an evidence check must not deadlock on its
# own flock), plus the flock itself across processes.
_LEDGER_LOCKS: dict = {}
_LEDGER_GUARD = threading.RLock()


@contextlib.contextmanager
def ledger_lock(repo):
    """The ONE critical section over the approval ledger and the approval
    records it describes: a blocking ``fcntl.flock`` on a lock file beside
    the ledger (the discipline of ``grok_bot.index.RequestIndex
    .serialized``). It is held across every ledger read-and-append, every
    mint (the record write AND its ledger entry) and every retirement (the
    record read, its removal AND its ledger entry), so two legitimate
    writers can never chain the same sequence number. Re-entrant within one
    thread; other threads of the process wait on a process-local lock, and
    other processes on the flock. A repository with no herd state directory
    has no ledger or records to coordinate, and nothing is created for it."""
    if not hroot(repo).is_dir():
        yield
        return
    path = hroot(repo) / LEDGER_LOCK
    key = str(path)
    with _LEDGER_GUARD:
        held = _LEDGER_LOCKS.get(key)
        if held is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(descriptor)
                raise
            held = _LEDGER_LOCKS[key] = [descriptor, 0]
        held[1] += 1
        try:
            yield
        finally:
            held[1] -= 1
            if held[1] == 0:
                del _LEDGER_LOCKS[key]
                try:
                    fcntl.flock(held[0], fcntl.LOCK_UN)
                finally:
                    os.close(held[0])


def _entry_digest(entry: dict) -> str:
    return hashlib.sha256(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ledger_entries(repo):
    """The ledger's entries, or None when there is no ledger. Raises
    ValueError when it cannot be read or its hash chain is broken."""
    path = ledger_path(repo)
    if not path.exists():
        return None
    entries, previous = [], LEDGER_GENESIS
    for number, line in enumerate(path.read_text().splitlines(), 1):
        entry = json.loads(line)
        if (
            not isinstance(entry, dict)
            or entry.get("prev_sha256") != previous
            or entry.get("seq") != number
        ):
            raise ValueError("entry %d does not chain" % number)
        previous = _entry_digest(entry)
        entries.append(entry)
    return entries


def token_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chained(entries, event, kind, digest, expires_at):
    return {
        "seq": len(entries) + 1,
        "event": event,
        "kind": kind,
        "token_sha256": digest,
        "expires_at": expires_at,
        "at": int(time.time()),
        "prev_sha256": (
            _entry_digest(entries[-1]) if entries else LEDGER_GENESIS
        ),
    }


# The single write primitive ``_write_synced`` uses (a seam, so the
# short-write tests can substitute it without touching ``os``).
_write = os.write


def _write_synced(target, data):
    """Create ``target`` (never an existing file) holding EXACTLY ``data``,
    flushed to disk before it is closed. ``os.write`` may write fewer bytes
    than asked, so every byte is accounted for: the write is repeated from
    where it stopped until all of ``data`` is written. A call that makes no
    progress, or that fails, refuses; the partial file is removed, never
    left looking complete, and nothing is retried forever."""
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            remaining = memoryview(data)
            while remaining:
                written = _write(descriptor, remaining)
                if not isinstance(written, int) or written <= 0:
                    raise OSError("no progress writing %s (%r bytes written,"
                                  " %d left)" % (target, written,
                                                 len(remaining)))
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise


def _replace_file(source, target):
    """The ONE publication step: an atomic rename over the active ledger."""
    os.replace(source, target)


def _archive_ledger(path, data):
    """A byte-for-byte COPY of the broken ledger. The ledger itself stays in
    place and in force."""
    archived = path.with_name(
        "approval-ledger.broken-%d-%s.jsonl"
        % (int(time.time()), secrets.token_hex(4)))
    _write_synced(archived, data)
    return archived


def _publish_ledger(path, lines):
    """Write the replacement chain to a temporary file beside the ledger,
    flush it, then publish it with one atomic rename. Until that rename the
    previous ledger is untouched; a temporary file left by a failure is
    removed (one left by a crash is inert: nothing reads it)."""
    temp = path.with_name(".approval-ledger.%s.tmp" % secrets.token_hex(8))
    try:
        _write_synced(temp, "".join(json.dumps(line, sort_keys=True) + "\n"
                                    for line in lines).encode("utf-8"))
        _replace_file(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _restart_broken_ledger(path, event, kind, digest, expires_at):
    """The human mint's in-band recovery from a ledger whose chain is broken
    or unreadable. The broken ledger stays the ACTIVE ledger until its
    replacement is safely published: (1) its bytes are COPIED to an archive
    (the original is not moved); (2) a fresh chain, a ``restart`` entry
    naming the broken bytes' digest followed by this mint, is written to a
    temporary file and flushed; (3) one atomic rename publishes it. An
    interruption or failure anywhere before (3) leaves the broken ledger in
    force, so every approval it made the guards refuse stays refused (round
    6: an earlier version moved the ledger away FIRST, which a failure
    before publication turned into "no ledger", the legacy no-evidence path:
    fail OPEN). Approvals minted in the old chain are not outstanding in the
    new one, so they are refused as tamper evidence until re-authorized."""
    data = path.read_bytes()
    restart = _chained([], "restart", "ledger", token_digest(data), None)
    entry = _chained([restart], event, kind, digest, expires_at)
    _archive_ledger(path, data)
    _publish_ledger(path, [restart, entry])


def ledger_append(repo, event, kind, digest, expires_at=None, create=False):
    """Append one chained entry, the read of the chain and the append as ONE
    operation under ``ledger_lock``. ``mint`` (only ``herdctl approve-*``)
    creates the ledger, and a mint that finds the chain broken restarts it
    (``_restart_broken_ledger``); a guard's ``consume``/``invalidate`` is
    recorded only into a ledger that already exists and never repairs one."""
    path = ledger_path(repo)
    with ledger_lock(repo):
        if not path.exists() and not create:
            return
        try:
            entries = _ledger_entries(repo) or []
        except ValueError:
            if not create:
                raise
            _restart_broken_ledger(path, event, kind, digest, expires_at)
            return
        entry = _chained(entries, event, kind, digest, expires_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _outstanding(entries, kind, now):
    """The token digests minted for ``kind`` and neither consumed,
    invalidated nor expired."""
    open_mints = {}
    for entry in entries:
        if entry.get("kind") != kind:
            continue
        if entry.get("event") == "mint":
            open_mints[entry.get("token_sha256")] = entry.get("expires_at")
        else:
            open_mints.pop(entry.get("token_sha256"), None)
    return set(
        digest for digest, expires_at in open_mints.items()
        if isinstance(expires_at, int) and expires_at >= now
    )


def ledger_evidence(repo, kind, token_bytes):
    """``None`` when the ledger shows nothing wrong (or there is no
    ledger), else the tamper-evidence reason. ``token_bytes`` is the
    approval record's bytes, or None when the record is absent."""
    try:
        with ledger_lock(repo):
            entries = _ledger_entries(repo)
    except (OSError, ValueError) as exc:
        return (
            f"Tamper evidence: the approval ledger is unreadable or its "
            f"chain is broken ({exc}). Re-authorize (`herdctl approve-*`"
            f" archives the broken ledger intact and publishes a new one)."
        )
    if entries is None:
        return None
    outstanding = _outstanding(entries, kind, int(time.time()))
    if token_bytes is None:
        if outstanding:
            return (
                f"Tamper evidence: an outstanding {kind} approval minted by "
                f"herdctl is missing (deleted?). Re-authorize."
            )
        return None
    if token_digest(token_bytes) not in outstanding:
        return (
            f"Tamper evidence: this {kind} approval is not an outstanding "
            f"herdctl mint (edited, already used, or written by something "
            f"else). Re-authorize."
        )
    return None


def retire_approval(path: Path, repo, kind: str, event: str) -> None:
    """Remove an approval record, recording why (``consume`` or
    ``invalidate``) in an EXISTING ledger. Every consumer of an approval
    retires it through here, so the ledger never shows a used approval as
    still outstanding."""
    _retire(path, repo, kind, event)


def _retire(path: Path, repo, kind: str, event: str) -> None:
    """Remove an approval record, recording why in an EXISTING ledger: the
    read, the removal and the entry as one operation under ``ledger_lock``,
    so a concurrent mint or retirement cannot interleave with it."""
    with ledger_lock(repo):
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        path.unlink(missing_ok=True)
        if data is not None:
            try:
                ledger_append(repo, event, kind, token_digest(data))
            except (OSError, ValueError):
                pass


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
        evidence = ledger_evidence(repo, KIND_COMMIT, None)
        return (
            False,
            evidence or "No approval exists. Run `herdctl approve-commit`.",
        )

    try:
        data = path.read_bytes()
        token = json.loads(
            data.decode("utf-8")
        )
    except Exception:
        return (
            False,
            "Approval token is unreadable. Re-authorize.",
        )

    evidence = ledger_evidence(repo, KIND_COMMIT, data)

    if evidence:
        return False, evidence

    if int(
        token.get("expires_at", 0)
    ) < int(time.time()):
        _retire(path, repo, KIND_COMMIT, "invalidate")
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
            _retire(path, repo, KIND_COMMIT, "invalidate")
            return (
                False,
                f"Approval invalidated because "
                f"`{key}` changed. Re-authorize.",
            )

    if consume:
        _retire(path, repo, KIND_COMMIT, "consume")

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
        evidence = ledger_evidence(repo, KIND_PUSH, None)
        return (
            False,
            evidence or "No push approval exists. Run `herdctl approve-push`.",
        )

    try:
        data = path.read_bytes()
        token = json.loads(
            data.decode("utf-8")
        )
    except Exception:
        return (
            False,
            "Push approval token is unreadable. Re-authorize.",
        )

    evidence = ledger_evidence(repo, KIND_PUSH, data)

    if evidence:
        return False, evidence

    if int(
        token.get("expires_at", 0)
    ) < int(time.time()):
        _retire(path, repo, KIND_PUSH, "invalidate")
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
        _retire(path, repo, KIND_PUSH, "invalidate")
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
            _retire(path, repo, KIND_PUSH, "invalidate")
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
        _retire(path, repo, KIND_PUSH, "consume")

    return True, "approved"


# -- the separate human merge gate ----------------------------------------

# Git global options that take a value, so the subcommand is found after it.
_GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                      "--exec-path", "--config-env")
# ``git merge`` options that take a value, so the source is found after it.
_MERGE_VALUE_OPTIONS = ("-m", "--message", "-F", "--file", "-s", "--strategy",
                        "-X", "--strategy-option", "--into-name", "--cleanup",
                        "-S", "--gpg-sign")

# What a delivery-shaped command is, by kind. Only LOCAL_MERGE has an
# approval kind here; every other kind is refused outright.
LOCAL_MERGE = "local_merge"
PR_CREATE = "pr_create"
PR_MERGE = "pr_merge"
PULL = "pull"
PULLS_API = "pulls_api"
NO_APPROVAL_KIND = {
    PR_CREATE: "opening a pull request is its own delivery action, and no"
               " approval kind for it exists in this repository's guards",
    PR_MERGE: "a remote pull-request merge's base cannot be re-checked"
              " locally where it is used, so no approval kind for it exists",
    PULL: "what `git pull` merges is unknown before it fetches; fetch, then"
          " merge the exact commit under a merge approval",
    PULLS_API: "conservative over-refusal: a `gh api` call naming pulls or"
               " merges (reads included) has no approval kind here",
}


def _git_subcommand(tokens, start):
    """``(subcommand, its position)`` after ``git`` at ``start``."""
    position = start + 1
    while position < len(tokens):
        token = tokens[position]
        if token in _GIT_VALUE_OPTIONS:
            position += 2
            continue
        if token.startswith("-"):
            position += 1
            continue
        return token, position
    return None, None


def _merge_sources(tokens, position):
    """The source arguments of the ``git merge`` at ``position``."""
    sources, index = [], position + 1
    while index < len(tokens):
        token = tokens[index]
        if token in ("&&", "||", ";", "|", "&"):
            break
        if token in _MERGE_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        sources.append(token)
        index += 1
    return sources


def _delivery_kinds(tokens, depth):
    found = []
    for position, token in enumerate(tokens):
        name = Path(token.lstrip("($`'\"")).name
        if name == "git":
            subcommand, at = _git_subcommand(tokens, position)
            if subcommand == "merge":
                found.append((LOCAL_MERGE, _merge_sources(tokens, at)))
            elif subcommand == "pull":
                found.append((PULL, None))
        elif name == "gh":
            rest = tokens[position + 1:]
            if rest[:2] == ["pr", "create"]:
                found.append((PR_CREATE, None))
            elif rest[:2] == ["pr", "merge"]:
                found.append((PR_MERGE, None))
            elif rest[:1] == ["api"] and any(
                "/pulls" in item or "/merge" in item for item in rest
            ):
                found.append((PULLS_API, None))
        if depth and any(char.isspace() for char in token):
            try:
                inner = shlex.split(token, posix=True)
            except ValueError:
                found.append((PULL, None))
                continue
            found.extend(_delivery_kinds(inner, depth - 1))
    return found


def delivery_kinds(command: str):
    """Every merge- or pull-request-shaped operation a shell command names,
    as ``(kind, merge sources or None)``, also inside one quoted ``sh -c``
    level. An unparsable command naming merge or pull counts as one with no
    approval kind: fail closed. A workflow guardrail's recogniser, not a
    boundary."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return [(PULL, None)] if re.search(r"\b(merge|pull)\b", command) else []
    return _delivery_kinds(tokens, 2)


def merge_shaped(command: str) -> bool:
    return bool(delivery_kinds(command))


def resolve_commit(repo, revision):
    """The full commit id ``revision`` names now, or None (read-only Git)."""
    if not isinstance(revision, str) or not revision or revision.startswith("-"):
        return None
    commit = gitout(repo, "rev-parse", "--verify", "--quiet",
                    revision + "^{commit}", allow_fail=True)
    return commit if re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", commit or "") else None


def merge_identity(
    repo: str | Path,
) -> dict:
    ident = repo_identity(repo)
    return dict(
        (key, ident[key]) for key in MERGE_IDENTITY_KEYS
    )


def _merge_token(repo):
    """``(token, None)`` for a merge approval that is a local merge
    approval, ledger-consistent, unexpired and still for this DESTINATION
    (repository, Git directory, branch, HEAD); else ``(None, reason)``. An
    expired or destination-mismatched approval is retired. It says nothing
    about the SOURCE: each layer checks that only where it can."""
    path = merge_approval_path(repo)

    if not path.exists():
        evidence = ledger_evidence(repo, KIND_MERGE, None)
        return None, (
            evidence or "No merge approval exists. Run `herdctl approve-merge"
                        " --source REV`."
        )

    try:
        data = path.read_bytes()
        token = json.loads(data.decode("utf-8"))
    except Exception:
        return None, "Merge approval token is unreadable. Re-authorize."

    if not isinstance(token, dict) or token.get("kind") != KIND_MERGE or (
        token.get("operation") != MERGE_OPERATION_LOCAL
    ):
        return None, "This is not a local merge approval. Run `herdctl approve-merge`."

    evidence = ledger_evidence(repo, KIND_MERGE, data)

    if evidence:
        return None, evidence

    if int(token.get("expires_at", 0)) < int(time.time()):
        _retire(path, repo, KIND_MERGE, "invalidate")
        return None, "Merge approval expired. Re-authorize."

    current = merge_identity(repo)

    for key in MERGE_IDENTITY_KEYS:
        if token.get(key) != current.get(key):
            _retire(path, repo, KIND_MERGE, "invalidate")
            return None, (
                f"Merge approval invalidated because `{key}` changed. "
                f"Re-authorize."
            )

    return token, None


def merge_approval_valid(
    repo: str | Path,
    source_commit: str | None = None,
    consume: bool = False,
):
    """The merge approval checked against a SOURCE commit the caller could
    establish: the destination checks of ``_merge_token`` plus equality with
    the approved source commit (a merge with no single resolvable source is
    refused, and a mismatch retires the approval)."""
    token, reason = _merge_token(repo)

    if token is None:
        return False, reason

    if source_commit is None or source_commit != token.get("source_commit"):
        _retire(merge_approval_path(repo), repo, KIND_MERGE, "invalidate")
        return (
            False,
            "Merge approval invalidated: the merge source is not the approved"
            f" commit {token.get('source_commit')} ({token.get('source_ref')})."
            " Re-authorize.",
        )

    if consume:
        _retire(merge_approval_path(repo), repo, KIND_MERGE, "consume")

    return True, "approved"


_OID = r"[0-9a-f]{40}([0-9a-f]{24})?"


def _merge_head(repo):
    """The source commit of a merge in progress (``MERGE_HEAD``), or None.
    It exists for a CONFLICTED merge being completed by ``git commit``; it
    does NOT exist yet when ``pre-merge-commit`` runs for a fresh automatic
    merge (see ``guard_premerge``)."""
    heads = gitout(repo, "rev-parse", "--verify", "--quiet", "MERGE_HEAD",
                   allow_fail=True)
    return heads if re.fullmatch(_OID, heads or "") else None


def _parents(repo, oid):
    """The parent commit ids of ``oid`` (read-only Git), or None."""
    line = gitout(repo, "rev-list", "--parents", "-n", "1", oid,
                  allow_fail=True)
    ids = (line or "").split()
    if not ids or ids[0] != oid or not all(re.fullmatch(_OID, i) for i in ids):
        return None
    return ids[1:]


def _is_ancestor(repo, ancestor, descendant):
    """Whether ``ancestor`` is an ancestor of ``descendant`` (``git
    merge-base --is-ancestor``, a local read; anything but exit 0 is no)."""
    result = run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                  ancestor, descendant])
    return result.returncode == 0


def _identifiable_merge(repo, head_updates):
    """Whether a branch update is identifiable AS A MERGE at the ref level,
    judged before and independently of any commit approval: exactly one
    update to a commit with two or more parents (a merge commit), or a
    fast-forward of more than one commit (``old`` an ancestor of ``new``,
    and ``new``'s parents are not just ``old``). A fast-forward by exactly
    one commit whose parent is ``old`` looks the same as a commit here and is
    judged as one; an update with no single new commit is not a merge."""
    if len(head_updates) != 1:
        return False
    old, new = head_updates[0]
    if not re.fullmatch(_OID, old or "") or not re.fullmatch(_OID, new or ""):
        return False
    if old == "0" * len(old) or new == "0" * len(new):
        return False
    parents = _parents(repo, new)
    if parents is None:
        return False
    if len(parents) >= 2:
        return True
    return parents != [old] and _is_ancestor(repo, old, new)


def _is_approved_merge_update(token, old, new, repo):
    """Whether moving the branch from ``old`` to ``new`` IS the approved
    merge: from the approved HEAD, either a TRUE fast-forward to the
    approved source commit (the approved HEAD must be an ancestor of it: a
    move to a source that is not a descendant would discard HEAD's commits,
    which is not the approved merge), or a commit whose parents are exactly
    (approved HEAD, approved source commit)."""
    head, source = token.get("head"), token.get("source_commit")
    if not head or not source or old != head:
        return False
    if new == source:
        return _is_ancestor(repo, old, new)
    return _parents(repo, new) == [head, source]


def merge_update_decision(repo, head_updates):
    """The ref-update layer (``reference-transaction``, phase prepared):
    ``(ok, message)`` for a branch update authorized by the merge approval.
    This is where the ACTUAL operation is known (the old and new commit, and
    the new commit's parents), so the source binding is enforced here for
    every way a merge reaches the ref update, not only through an agent's
    tool call."""
    if len(head_updates) != 1:
        return False, "a merge approval covers exactly one branch update"
    token, reason = _merge_token(repo)
    if token is None:
        return False, reason
    old, new = head_updates[0]
    if not _is_approved_merge_update(token, old, new, repo):
        # The same discipline as every other identity mismatch: the
        # approval is RETIRED, so a correctly shaped retry needs a fresh one.
        _retire(merge_approval_path(repo), repo, KIND_MERGE, "invalidate")
        return False, (
            "this branch update is not the approved merge of"
            f" {token.get('source_commit')} into {token.get('head')};"
            " the merge approval is invalidated. Re-authorize."
        )
    return True, "the approved merge"


def _consume_merge_on_commit(repo, head_updates):
    """Phase committed: retire the merge approval as consumed when the
    committed update IS the approved merge. (HEAD has moved now, so the
    destination check would no longer pass; the update itself is compared.)"""
    path = merge_approval_path(repo)
    if len(head_updates) != 1 or not path.exists():
        return
    try:
        token = json.loads(path.read_text())
    except Exception:
        return
    old, new = head_updates[0]
    if isinstance(token, dict) and _is_approved_merge_update(token, old, new, repo):
        _retire(path, repo, KIND_MERGE, "consume")


def guard_premerge(
    repo: str | Path,
) -> int:
    """The ``pre-merge-commit`` hook, which checks only what it can
    establish at that point. In upstream Git's ``builtin/merge.c``
    (``prepare_to_commit``), this hook runs BEFORE ``write_merge_heads``
    writes ``MERGE_HEAD``, so for a fresh automatic merge the source is not
    visible here (source-based inference from current upstream Git, not a
    test of the installed Git version). So this checks that a merge approval
    exists for this destination (repository, Git directory, branch, HEAD),
    unexpired and ledger-consistent, and does NOT consume it. The source is
    bound by the pretool check (from the command's arguments) and, for every
    path, by the ref-update check (``merge_update_decision``), which also
    consumes it."""
    if not (hroot(repo) / CFG).exists():
        print(
            f"HERD MERGE BLOCKED: {repo} is not initialized for herd merge"
            " confirmation.",
            file=sys.stderr,
        )
        return 1

    token, reason = _merge_token(repo)

    if token is None:
        print(f"HERD MERGE BLOCKED: {reason}", file=sys.stderr)
        return 1

    return 0


_SHELL_OPERATOR_CHARS = frozenset("();<>|&")


def _merge_command_refusal(command):
    """Why a merge-shaped ``command`` is not ONE standalone ``git merge ...``
    run in the working directory's repository, or None.

    The pretool judges the merge against the repository of the payload's
    working directory. Anything that can make the merge run in ANOTHER
    repository would have it judged against the wrong identity, so every
    such form is refused conservatively, as ``git -C`` already is for
    commit and push: a global option before ``merge`` (``-C``,
    ``--git-dir``, ``--work-tree``, ``-c ...``), an environment prefix
    (``GIT_DIR=...``), a ``cd`` or any other chained command, a nested
    shell, and shell substitution. Variables already exported in the
    agent's shell are not visible here (a stated residual)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return "it could not be parsed safely"
    if any(token and set(token) <= _SHELL_OPERATOR_CHARS for token in tokens):
        return "it is chained with other shell operations"
    if any("`" in token or "$" in token for token in tokens):
        return "it contains shell substitution"
    if len(tokens) < 2 or Path(tokens[0]).name != "git" or tokens[1] != "merge":
        return ("it is not a standalone `git merge ...` (repository"
                " redirection such as `git -C`, `--git-dir`, `--work-tree`, an"
                " environment prefix, a nested shell, or any global option"
                " before `merge`)")
    return None


def _guard_delivery_kinds(command, repo):
    """The pretool decision for merge- and pull-request-shaped commands:
    ``(refused, message)``."""
    for kind, sources in delivery_kinds(command):
        if kind in NO_APPROVAL_KIND:
            return True, (
                f"Refused: {NO_APPROVAL_KIND[kind]}. No approval opens it;"
                " a merge approval authorizes only a local merge."
            )
        refusal = _merge_command_refusal(command)
        if refusal:
            return True, (
                f"Merge blocked: {refusal}. A merge approval is checked"
                " against this working directory's repository, so the merge"
                " must run here as one standalone `git merge ...`; anything"
                " that could run it in another repository is refused."
            )
        if not repo:
            return True, "Merge blocked: unable to identify repository."
        if not (hroot(repo) / CFG).exists():
            return True, (
                f"Merge blocked: {repo} is not initialized for herd merge"
                " confirmation."
            )
        if len(sources) != 1:
            return True, (
                "Merge blocked: name exactly one source to merge; a merge"
                " approval binds one source commit."
            )
        valid, message = merge_approval_valid(
            repo, source_commit=resolve_commit(repo, sources[0]))
        if not valid:
            return True, f"Merge blocked for {Path(repo).name}: {message}"
    return False, ""


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

    if "git" not in command and "gh" not in command:
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

    refused, message = _guard_delivery_kinds(command, repo)

    if refused:
        print(message, file=sys.stderr)
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
    merge_head = _merge_head(repo)

    if merge_head is not None:
        # A CONFLICTED merge completed by ``git commit`` runs this hook, not
        # ``pre-merge-commit`` (githooks); here MERGE_HEAD exists, so this
        # commit IS a merge. It needs the merge approval for exactly that
        # source, decided FIRST and independently: a valid commit approval
        # never stands in for it. Not consumed here: the ref update consumes
        # it once it is exactly the approved merge.
        merge_ok, merge_message = merge_approval_valid(
            repo,
            source_commit=merge_head,
        )

        if merge_ok:
            print(
                f"HERD MERGE COMPLETION PRE-CHECK AUTHORIZED: "
                f"{Path(repo).resolve()}",
                file=sys.stderr,
            )
            return 0

        print(
            "HERD MERGE BLOCKED: completing a merge needs the merge approval"
            " for exactly its source; a commit approval never authorizes a"
            f" merge ({merge_message})",
            file=sys.stderr,
        )
        return 1

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

    head_updates = [
        (old, new)
        for old, new, ref in updates
        if ref == head_ref
    ]

    if phase == "prepared":
        # The separate merge gate FIRST, independent of any commit approval:
        # an update identifiable as a merge needs merge authority for exactly
        # it, and a valid commit approval never stands in for that.
        if _identifiable_merge(repo, head_updates):
            merge_ok, merge_message = merge_update_decision(
                repo,
                head_updates,
            )

            if merge_ok:
                return 0

            # pr_delivery's executing BASE_REFRESH receipt for EXACTLY this
            # update line is the only other authority for it.
            old_oid, new_oid = head_updates[0]
            receipt_ok, receipt_message = _delivery_receipt_decision(
                repo,
                DELIVERY_STEP_BASE_REFRESH,
                {
                    "repository_realpath": str(Path(repo).resolve()),
                    "source_ref": head_ref,
                    "old_base_oid": old_oid,
                    "new_base_oid": new_oid,
                },
            )

            if receipt_ok:
                return 0

            print(
                "HERD MERGE BLOCKED: this branch update is a merge and needs"
                " the merge approval for exactly it; a commit approval never"
                f" authorizes a merge ({merge_message}; delivery receipt:"
                f" {receipt_message})",
                file=sys.stderr,
            )
            return 1

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
        _consume_merge_on_commit(repo, head_updates)
        _retire(approval_path(repo), repo, KIND_COMMIT, "consume")
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
        _retire(path, repo, KIND_PUSH, "consume")
        return

    remote_name = token.get("remote_name")
    branch = str(
        token.get("target_ref", "")
    ).removeprefix("refs/heads/")
    head = token.get("head")

    if not remote_name or not branch or not head:
        _retire(path, repo, KIND_PUSH, "consume")
        return

    tracking_ref = f"refs/remotes/{remote_name}/{branch}"

    for _old_oid, new_oid, ref in updates:
        if ref == tracking_ref and new_oid == head:
            _retire(path, repo, KIND_PUSH, "consume")
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

    _install_one_git_hook(
        repo,
        "pre-merge-commit",
        "# HERD MERGE GUARD v0.1",
        (
            prefix
            + ' premerge --repo-path "$ROOT"'
        ),
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
        "premerge"
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

        elif args.command == "premerge":
            code = guard_premerge(
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
