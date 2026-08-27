"""Terminal CLI for the DI-REMOTE-2 Runtime (``dirun``).

Subcommands: ``once`` (one claim pass) and ``run`` (poll loop). The
Runtime holds its single-instance lock (the same file /status probes)
for its whole life, constructs the REAL git transport and the REAL
role-turn runner unconditionally — there is NO transport override of
any kind: no environment variable, no CLI flag, no config key (the
static suite proves it) — and reuses the adapter's protected
configuration for the control repository path and state directory.

Exit codes: 0 success, 2 configuration problem, 3 another Runtime
instance already holds the lock.
"""

import argparse
import fcntl
import os
import sys
import time

from codex_gateway import role_turn as role_turn_module
from telegram_operator.config import ConfigError, load_config
from telegram_operator.state import (
    RUNTIME_LOCK_FILE_NAME,
    default_state_dir,
)

from target_runtime import runtime as runtime_module
from target_runtime.broker import TargetBroker
from target_runtime.git_transport import GitTransport
from target_runtime.workspace import default_workspaces_root

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOCKED = 3

# Bounded client-side wait between claim passes (ruling N-2: bounded
# waits where genuinely needed; this is pacing, not a mission
# timeout — nothing is ever cancelled by it).
RUNTIME_POLL_INTERVAL_SECONDS = 5


def acquire_runtime_lock(state_directory):
    """Hold the Runtime's single-instance lock, or return None.

    This is the exact lock /status probes: holding it is what makes
    the Runtime visible as running.
    """
    os.makedirs(state_directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(state_directory, RUNTIME_LOCK_FILE_NAME)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="dirun",
        description=(
            "DI-REMOTE-2 Runtime: claim durably authorized missions"
            " from the workflow store and advance them through the"
            " fixed target lifecycle. Carries NO delivery authority:"
            " no commit, push, PR, tag, release, deploy, or merge can"
            " result from anything this process does."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="adapter config file path (default: the protected"
        " per-user location); supplies the control repository path"
        " and the state directory",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("once", help="run one claim pass and exit")
    subparsers.add_parser(
        "run", help="run the claim loop in the foreground"
    )
    return parser


def production_role_turn(role, entry, now, target_context=None,
                         observation=None, evidence=None):
    """The PRODUCTION role-turn seam the Broker is wired with.

    Module-level and signature-pinned (I4 revision 1): its keyword
    surface must accept EVERY keyword any Broker call site passes —
    a derivation test AST-walks the Broker's `self._role_turn(...)`
    call sites and asserts each derived keyword is accepted here,
    and the hermetic test double is pinned NO WIDER than this
    signature, so a seam mismatch can no longer be hermetically
    invisible. (The pre-revision wrapper accepted only
    `target_context`; the `observation=` keyword shipped by the
    prior accepted task and `evidence=` added by I3 made every
    production verification pass raise TypeError — caught only when
    routed, because the wider double hid it.)
    """
    return role_turn_module.run_role_turn(
        role, entry, now, target_context=target_context,
        observation=observation, evidence=evidence,
    )


def _build_broker(namespace):
    config_path = (
        os.path.abspath(namespace.config) if namespace.config else None
    )
    loaded = load_config(config_path)
    state_directory = (
        os.path.dirname(config_path)
        if config_path else default_state_dir()
    )
    workspaces_root = os.path.join(
        state_directory, "workspaces"
    ) if config_path else default_workspaces_root()

    broker = TargetBroker(
        store_directory=state_directory,
        control_repository_realpath=loaded.repository,
        transport=GitTransport(),
        workspaces_root=workspaces_root,
        role_turn_fn=production_role_turn,
    )
    return broker, state_directory


def main(argv=None, sleeper=None, passes=None):
    """CLI entry. ``sleeper``/``passes`` exist ONLY for tests (an
    injected pacing bound); production callers pass nothing and run
    unbounded — pacing, not a mission timeout."""
    parser = _build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG
    if namespace.command not in ("once", "run"):
        parser.print_help(sys.stderr)
        return EXIT_CONFIG
    try:
        broker, state_directory = _build_broker(namespace)
    except ConfigError as exc:
        print("dirun: config: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    lock_descriptor = acquire_runtime_lock(state_directory)
    if lock_descriptor is None:
        print(
            "dirun: another Runtime instance already holds the lock"
            " in %s; refusing to run twice" % state_directory,
            file=sys.stderr,
        )
        return EXIT_LOCKED
    try:
        if namespace.command == "once":
            processed = runtime_module.process_once(broker)
            print(
                "dirun: processed %d workflow(s) (exact)"
                % len(processed),
                file=sys.stderr,
            )
            return EXIT_OK
        pause = sleeper or time.sleep
        remaining = passes
        while True:
            runtime_module.process_once(broker)
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    return EXIT_OK
            pause(RUNTIME_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        os.close(lock_descriptor)
