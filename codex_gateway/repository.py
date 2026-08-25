"""Target repository validation for the Codex Gateway.

Validation runs BEFORE any Codex invocation and is strictly read-only.
A valid target must resolve to an existing directory, sit inside a git
worktree, and carry the operator contract files (AGENTS.md and
OPERATOR_PROTOCOL.md) at the resolved repository root. Every failure
names the check that failed and the path that was checked.
"""

import os
import subprocess

from codex_gateway.contract import (
    ERROR_GIT_UNAVAILABLE,
    ERROR_NOT_A_GIT_WORKTREE,
    ERROR_OPERATOR_CONTRACT_MISSING,
    ERROR_REPOSITORY_MISSING,
    make_error,
)

REQUIRED_OPERATOR_FILES = ("AGENTS.md", "OPERATOR_PROTOCOL.md")


def resolve_repository_path(path):
    """Pure path normalization: absolute with symlinks resolved."""
    return os.path.abspath(os.path.realpath(str(path)))


def validate_repository(path):
    """Validate a target repository.

    Returns ``(resolved_path, None)`` on success or ``(None, GatewayError)``
    on failure. Never invokes Codex; the only subprocess is a read-only
    ``git rev-parse`` run as an argv list without a shell.
    """
    if path is None or not str(path).strip():
        return None, make_error(
            ERROR_REPOSITORY_MISSING,
            "repository check failed: no repository path was provided",
        )
    resolved = resolve_repository_path(path)
    if not os.path.isdir(resolved):
        return None, make_error(
            ERROR_REPOSITORY_MISSING,
            "repository check failed: %s does not exist or is not a directory"
            % resolved,
        )
    try:
        # Streams captured as bytes: the decode below is explicit and
        # non-strict-crash-proof (escapes are shown visibly rather than
        # raising), so undecodable git output can never traceback.
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=resolved,
            capture_output=True,
        )
    except OSError as exc:
        return None, make_error(
            ERROR_GIT_UNAVAILABLE,
            "git worktree check failed for %s: git could not be executed"
            " (%s); install git and ensure it is on PATH" % (resolved, exc),
        )
    if probe.returncode != 0 or probe.stdout.strip() != b"true":
        said = (
            (probe.stdout + probe.stderr)
            .decode("utf-8", "backslashreplace")
            .strip()
            or "(no output)"
        )
        return None, make_error(
            ERROR_NOT_A_GIT_WORKTREE,
            "git worktree check failed: %s is not inside a git worktree"
            " (git rev-parse --is-inside-work-tree said: %s)" % (resolved, said),
        )
    for name in REQUIRED_OPERATOR_FILES:
        candidate = os.path.join(resolved, name)
        if not os.path.isfile(candidate):
            return None, make_error(
                ERROR_OPERATOR_CONTRACT_MISSING,
                "operator contract check failed: required file %s not found"
                " at %s" % (name, candidate),
            )
    return resolved, None
