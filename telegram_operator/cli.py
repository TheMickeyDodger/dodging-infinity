"""Terminal CLI for the Telegram Remote Operator adapter.

Subcommands: ``run`` (foreground adapter), ``install-agent`` /
``uninstall-agent`` (optional per-user LaunchAgent). The CLI loads
config from outside the repository, takes the single-instance lock,
and starts the adapter; it interprets no approval, mission, or
delivery semantics itself and touches no orchestration state.

Exit codes: 0 success, 2 configuration or usage problem, 3 another
adapter instance already holds the single-instance lock, 4 agent
install/uninstall failure.
"""

import argparse
import os
import sys

from telegram_operator import launchagent
from telegram_operator.adapter import Adapter
from telegram_operator.config import ConfigError, load_config
from telegram_operator.state import (
    StateError,
    StateStore,
    acquire_single_instance_lock,
    default_state_dir,
)
from telegram_operator.telegram_api import TelegramApi

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOCKED = 3
EXIT_AGENT = 4


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="tgop",
        description=(
            "Telegram Remote Operator adapter: route allowlisted"
            " private-chat Telegram intent into the local Codex"
            " Operator workflow. Delivery authority (commit/push/PR/"
            "tag/release) is deferred and never granted remotely."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="config file path (default: the protected per-user"
        " location under ~/Library/Application Support)",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="run the adapter in the foreground")
    subparsers.add_parser(
        "install-agent",
        help="install and load the optional per-user LaunchAgent",
    )
    subparsers.add_parser(
        "uninstall-agent",
        help="unload and remove the per-user LaunchAgent",
    )
    return parser


def _run(namespace):
    # A relative --config must be normalized BEFORE permission checks
    # and state-dir derivation: dirname("config.json") is "", and an
    # empty state dir is a traceback, not a diagnostic (round-4
    # finding OP4).
    config_path = (
        os.path.abspath(namespace.config) if namespace.config else None
    )
    try:
        loaded = load_config(config_path)
    except ConfigError as exc:
        print("tgop: config: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    state_dir = (
        os.path.dirname(config_path)
        if config_path else default_state_dir()
    )
    lock_descriptor = acquire_single_instance_lock(state_dir)
    if lock_descriptor is None:
        print(
            "tgop: another adapter instance already holds the lock in"
            " %s; refusing to run twice (two pollers would split"
            " updates and double-dispatch)" % state_dir,
            file=sys.stderr,
        )
        return EXIT_LOCKED
    try:
        store = StateStore(state_dir)
        adapter = Adapter(loaded, store, TelegramApi(loaded.bot_token))
        try:
            adapter.run()
        except KeyboardInterrupt:
            pass
        return EXIT_OK
    except StateError as exc:
        print("tgop: state: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    finally:
        os.close(lock_descriptor)


def main(argv=None):
    parser = _build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG
    if namespace.command == "run":
        return _run(namespace)
    if namespace.command == "install-agent":
        entry = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tgop.py",
        )
        # An explicit --config must reach the installed job verbatim;
        # silently installing an agent that runs the DEFAULT config —
        # a different token, allowlist, and repository — is round-4
        # finding R4-B2.
        config_path = (
            os.path.abspath(namespace.config)
            if namespace.config else None
        )
        # Validate the EXACT config the job will run BEFORE anything
        # is written or loaded (final round-5 operator finding): the
        # job is KeepAlive, so installing against a missing,
        # malformed, or unsafe-permission config would report success
        # and then restart-loop every ThrottleInterval as the adapter
        # exits on the config error at each launch. Same diagnostic
        # path and exit code as `run`.
        try:
            load_config(config_path)
        except ConfigError as exc:
            print("tgop: config: %s" % exc, file=sys.stderr)
            return EXIT_CONFIG
        ok, message = launchagent.install_agent(
            sys.executable, entry, config_path=config_path
        )
        print("tgop: %s" % message, file=sys.stderr)
        return EXIT_OK if ok else EXIT_AGENT
    if namespace.command == "uninstall-agent":
        ok, message = launchagent.uninstall_agent()
        print("tgop: %s" % message, file=sys.stderr)
        return EXIT_OK if ok else EXIT_AGENT
    parser.print_help(sys.stderr)
    return EXIT_CONFIG
