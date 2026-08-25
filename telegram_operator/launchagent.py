"""Optional per-user macOS LaunchAgent for the adapter.

Installation is strictly per-user: the plist lives in
``~/Library/LaunchAgents`` and nothing system-wide is touched. The
generated job uses absolute executable paths, RunAtLoad, KeepAlive with
a restart throttle, and log files inside the protected adapter state
directory (mode 0700). Uninstall removes exactly what install created.

KeepAlive restarts a process that EXITS. It cannot rescue a process
that is alive but stalled; see SECURITY.md for the transport-level
consequences of that distinction.

This module is import-safe on any platform (tests are hermetic and
inject the launchctl runner); actually loading an agent only makes
sense on macOS.
"""

import os
import plistlib
import shutil
import subprocess

from telegram_operator.state import default_state_dir

AGENT_LABEL = "com.dodginginfinity.telegram-operator"
# Minimum seconds launchd waits before relaunching after an exit
# (restart throttle). A hard constant.
THROTTLE_SECONDS = 10
# Fixed base PATH for the launchd job. launchd does NOT inherit the
# interactive shell PATH, and the Codex Gateway launches the `codex`
# binary by name, so the job's PATH is composed ONLY of these
# hard-coded directories plus the directory the codex executable
# resolves to at INSTALL time (round-4 review finding R4-B3). The
# ambient interactive PATH is never passed through — that would be an
# unbounded environment leak into the always-on job.
AGENT_BASE_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CODEX_BINARY_NAME = "codex"


def agent_state_dir(home=None, config_path=None):
    """The directory the installed job logs into and the daemon keeps
    its state and lock in: the custom config's own directory when one
    was named, otherwise the default protected state directory. One
    derivation, used by plist generation and directory preparation
    alike, so the two can never disagree."""
    if config_path is not None:
        return os.path.dirname(os.path.abspath(config_path))
    return default_state_dir(home)


def agent_plist_path(home=None):
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(
        base, "Library", "LaunchAgents", "%s.plist" % AGENT_LABEL
    )


def build_plist(python_executable, entry_script, home=None,
                config_path=None, codex_directory=None):
    """The LaunchAgent job definition, absolute paths only.

    ``config_path``, when given, is propagated as an absolute
    ``--config`` argument so the installed job runs against EXACTLY the
    configuration the operator named — silently falling back to the
    default config (a different token, allowlist, and repository)
    is round-4 finding R4-B2. The log paths live in the SAME directory
    the daemon derives its state and lock from — the custom config's
    directory when one was given, the default state directory
    otherwise — so logs, state, and lock never split across two
    locations (operator correction-pass, item 1).

    ``codex_directory`` (the install-time resolution of the codex
    binary) is placed FIRST in the job's PATH, ahead of the fixed
    AGENT_BASE_PATH constant, so the exact executable validated at
    install time is the one that wins even if a different ``codex``
    sits in one of the base directories (operator correction-pass,
    item 2).
    """
    state_dir = agent_state_dir(home, config_path)
    program_arguments = [
        os.path.abspath(python_executable),
        os.path.abspath(entry_script),
    ]
    if config_path is not None:
        program_arguments += ["--config", os.path.abspath(config_path)]
    program_arguments.append("run")
    if codex_directory:
        path_value = ":".join(
            [codex_directory] + [
                directory
                for directory in AGENT_BASE_PATH.split(":")
                if directory != codex_directory
            ]
        )
    else:
        path_value = AGENT_BASE_PATH
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": program_arguments,
        "EnvironmentVariables": {"PATH": path_value},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "StandardOutPath": os.path.join(state_dir, "tgop.out.log"),
        "StandardErrorPath": os.path.join(state_dir, "tgop.err.log"),
    }


def _default_runner(argv):
    completed = subprocess.run(argv, capture_output=True)
    return completed.returncode


def install_agent(python_executable, entry_script, home=None, runner=None,
                  config_path=None, which=None):
    """Write the plist (0600) and load it. Returns (ok, message).

    Fails closed, before writing anything, when the codex binary
    cannot be resolved on the CURRENT PATH: the installed job would
    otherwise fail every single Codex turn as codex_unavailable
    (round-4 finding R4-B3). The job must be reinstalled if the codex
    binary later moves.
    """
    run = runner or _default_runner
    resolve = which or shutil.which
    codex_path = resolve(CODEX_BINARY_NAME)
    if not codex_path:
        return False, (
            "the %r binary is not resolvable on the current PATH, so"
            " the installed agent could never complete a Codex turn."
            " Nothing was installed. Put codex on PATH and re-run"
            " install-agent." % CODEX_BINARY_NAME
        )
    codex_directory = os.path.dirname(os.path.abspath(codex_path))
    path = agent_plist_path(home)
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    # The job's StandardOutPath/StandardErrorPath point into the SAME
    # directory the daemon will use for state and lock (custom config
    # directory or default); launchd must be able to open the logs on
    # the very first launch, before tgop itself ever ran (round-4
    # finding R4-N1; operator correction-pass item 1).
    os.makedirs(
        agent_state_dir(home, config_path), mode=0o700, exist_ok=True
    )
    payload = plistlib.dumps(
        build_plist(
            python_executable, entry_script, home,
            config_path=config_path, codex_directory=codex_directory,
        )
    )
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    code = run(["launchctl", "load", "-w", path])
    if code != 0:
        return False, (
            "wrote %s but `launchctl load -w` exited %d; load it"
            " manually or check `launchctl list`" % (path, code)
        )
    return True, "installed and loaded %s" % path


def uninstall_agent(home=None, runner=None):
    """Unload the agent and remove its plist. Returns (ok, message)."""
    run = runner or _default_runner
    path = agent_plist_path(home)
    if not os.path.exists(path):
        return True, "nothing installed at %s" % path
    code = run(["launchctl", "unload", "-w", path])
    os.unlink(path)
    if code != 0:
        return True, (
            "removed %s; `launchctl unload -w` exited %d (the agent may"
            " not have been loaded)" % (path, code)
        )
    return True, "unloaded and removed %s" % path
