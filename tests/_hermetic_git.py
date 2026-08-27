"""Hermetic Git invocation helper for the test suite.

Every test-created Git operation that requires committer/author identity
(commit, merge, annotated tag, cherry-pick, revert, rebase, am, stash,
notes, commit-tree) MUST route through this module.  It injects an
explicit, invocation-local identity so test Git operations succeed with
no ambient Git identity of any kind — no ~/.gitconfig, no XDG config, no
system gitconfig, no GIT_AUTHOR_*/GIT_COMMITTER_* environment — on any
platform.  In particular it does not depend on Git's user@host identity
auto-detection, which works on macOS but fails on Ubuntu CI runners.

The identity is carried as ``-c`` options on the argv of each invocation
(never global or repository config), together with ``-c
commit.gpgsign=false`` so an operator's signing configuration can never
break or block a test commit.  ``-c user.name``/``-c user.email`` cover
both author and committer for every porcelain and plumbing subcommand.

tests/test_hermetic_git.py is the load-bearing guard: it proves by
execution that this helper commits successfully under a fully scrubbed
identity environment, and proves by AST scan that no identity-requiring
Git invocation in tests/ bypasses this module.

This module is deliberately not named ``test_*.py``: it is a helper, not
a test module, and must not be collected by the suite loop.
"""

import subprocess

IDENTITY_NAME = "T"
IDENTITY_EMAIL = "t@example.com"

# Invocation-local configuration injected into every Git call.
HERMETIC_GIT_ARGS = (
    "-c", "user.name=%s" % IDENTITY_NAME,
    "-c", "user.email=%s" % IDENTITY_EMAIL,
    "-c", "commit.gpgsign=false",
)


def hermetic_git_argv(argv):
    """The full argv for a hermetic Git invocation: ``git`` followed by
    the invocation-local identity/signing configuration, then the
    caller's arguments (which may begin with global options such as
    ``-C``/``--no-optional-locks``)."""
    return ["git"] + list(HERMETIC_GIT_ARGS) + list(argv)


def run_git(*argv, cwd=None, env=None):
    """Run a hermetic Git command, assert success, return stripped
    stdout.  Drop-in for the historical test_target_runtime.run_git."""
    completed = subprocess.run(
        hermetic_git_argv(argv), cwd=cwd, env=env,
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "test git failed: %s: %s" % (argv, completed.stderr)
        )
    return completed.stdout.strip()


def run_git_completed(argv, cwd=None, check=True, env=None):
    """Run a hermetic Git command and return the CompletedProcess.
    ``check`` follows subprocess.run semantics for callers that assert
    on return codes themselves."""
    return subprocess.run(
        hermetic_git_argv(argv), cwd=cwd, env=env,
        capture_output=True, text=True, check=check,
    )
