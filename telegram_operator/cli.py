"""Terminal CLI for the Telegram Remote Operator adapter.

Subcommands: ``run`` (foreground adapter), ``install-agent`` /
``uninstall-agent`` (optional per-user LaunchAgent),
``migrate-state`` (the explicit adapter-state schema 1 -> 2
migration; never automatic), and ``migrate-workflows`` (the explicit
workflow-store schema 1 -> 2 migration; never automatic; retires v1
records rather than fabricating authority fields), and
``runtime-service`` (the per-user LaunchAgent that supervises the DI
Runtime, ``dirun run``: install / status / start / stop / restart /
uninstall / doctor; a thin adapter over
``telegram_operator.runtime_service``). The CLI loads
config from outside the
repository, takes the single-instance lock, and starts the adapter;
it interprets no approval, mission, or delivery semantics itself and
touches no orchestration state.

Exit codes: 0 success, 2 configuration or usage problem, 3 another
adapter instance already holds the single-instance lock, 4 agent
install/uninstall failure (also runtime-service lifecycle failure).
"""

import argparse
import os
import sys

from telegram_operator import launchagent
from telegram_operator import runtime_service
from telegram_operator.adapter import Adapter
from telegram_operator.config import ConfigError, load_config
from telegram_operator.state import (
    StateError,
    StateStore,
    acquire_single_instance_lock,
    default_state_dir,
)
from telegram_operator.telegram_api import TelegramApi
from workflow_authority.migrate import (
    MIGRATION_COMMAND,
    WORKFLOW_MIGRATION_COMMAND,
    MigrationError,
    migrate_state,
    migrate_workflow_store,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOCKED = 3
EXIT_AGENT = 4

RUNTIME_SERVICE_ACTIONS = (
    "install", "status", "start", "stop", "restart", "uninstall", "doctor",
)


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
    subparsers.add_parser(
        "migrate-state",
        help="explicitly migrate the adapter state file from schema"
        " version 1 to 2 (keeps a preserved v1 backup; marks"
        " pre-existing approvals superseded for v2 purposes only)",
    )
    subparsers.add_parser(
        "migrate-workflows",
        help="explicitly migrate the workflow store from schema"
        " version 1 to 2 (RETIRES v1 workflow records into a"
        " preserved byte-exact backup — their missing authority"
        " fields are never fabricated; each retired workflow needs a"
        " fresh Mission Authorization)",
    )
    service = subparsers.add_parser(
        "runtime-service",
        help="supervise the DI Runtime (dirun run) as a per-user"
        " LaunchAgent: install, status, start, stop, restart,"
        " uninstall, doctor",
    )
    service.add_argument(
        "action",
        choices=RUNTIME_SERVICE_ACTIONS,
    )
    service.add_argument(
        "--root",
        metavar="PATH",
        default=None,
        help="stable production checkout the job binds (default:"
        " this checkout; refused when it is a temp path, a git"
        " worktree, or otherwise unstable)",
    )
    service.add_argument(
        "--reconcile",
        action="store_true",
        help="install only: explicitly replace an existing differing"
        " or unparseable job definition under the same label",
    )
    return parser


def _print_migrate_filesystem_error(exc, state_dir):
    # A read-only directory or a full disk surfaces as OSError — from
    # the very first filesystem touch (creating the lock file) or
    # from the migration itself. The state file is untouched
    # (fail-closed), so the user gets an actionable message, never a
    # traceback (round-2 review finding B4: the lock acquisition is
    # the first raiser on a read-only directory and must be guarded
    # too).
    print(
        "tgop: migrate: filesystem error (%s); the state file was"
        " left untouched. Fix the permissions or free space in %s"
        " and re-run '%s'" % (exc, state_dir, MIGRATION_COMMAND),
        file=sys.stderr,
    )


def _migrate_state(namespace):
    # The state directory derives from --config exactly as `run`
    # derives it, so the migration always targets the same state file
    # the adapter would load.
    config_path = (
        os.path.abspath(namespace.config) if namespace.config else None
    )
    state_dir = (
        os.path.dirname(config_path)
        if config_path else default_state_dir()
    )
    # Take the same single-instance lock the adapter holds: migrating
    # underneath a live adapter process would interleave writes on an
    # authority-bearing file.
    try:
        lock_descriptor = acquire_single_instance_lock(state_dir)
    except OSError as exc:
        _print_migrate_filesystem_error(exc, state_dir)
        return EXIT_CONFIG
    if lock_descriptor is None:
        print(
            "tgop: another adapter instance holds the lock in %s;"
            " stop it before migrating state" % state_dir,
            file=sys.stderr,
        )
        return EXIT_LOCKED
    try:
        changed, message = migrate_state(state_dir)
        del changed  # the message states exactly what happened
        print("tgop: %s" % message, file=sys.stderr)
        return EXIT_OK
    except MigrationError as exc:
        print("tgop: migrate: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    except OSError as exc:
        _print_migrate_filesystem_error(exc, state_dir)
        return EXIT_CONFIG
    finally:
        os.close(lock_descriptor)


def _migrate_workflows(namespace):
    # Same directory derivation as `run` and `migrate-state`: the
    # workflow store lives beside the adapter state in the protected
    # per-user directory (or beside an explicit --config).
    config_path = (
        os.path.abspath(namespace.config) if namespace.config else None
    )
    store_dir = (
        os.path.dirname(config_path)
        if config_path else default_state_dir()
    )
    try:
        changed, message = migrate_workflow_store(store_dir)
        del changed  # the message states exactly what happened
        print("tgop: %s" % message, file=sys.stderr)
        return EXIT_OK
    except MigrationError as exc:
        print("tgop: migrate: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    except OSError as exc:
        # Same fail-closed posture as migrate-state: the store file
        # is untouched on a filesystem error.
        print(
            "tgop: migrate: filesystem error (%s); the workflow store"
            " was left untouched. Fix the permissions or free space"
            " in %s and re-run '%s'"
            % (exc, store_dir, WORKFLOW_MIGRATION_COMMAND),
            file=sys.stderr,
        )
        return EXIT_CONFIG


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


def _repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _runtime_service(namespace):
    # Thin adapter: parse, call, print, map to exit codes. Every
    # decision (stable root, reconcile, state machine) lives in
    # telegram_operator.runtime_service.
    config_path = (
        os.path.abspath(namespace.config) if namespace.config else None
    )
    action = namespace.action
    if action == "install":
        root = (
            os.path.abspath(namespace.root) if namespace.root
            else _repository_root()
        )
        # Validate the EXACT config the KeepAlive job will run BEFORE
        # anything is written (the install-agent precedent): same
        # diagnostic path and exit code as `run`.
        try:
            load_config(config_path)
        except ConfigError as exc:
            print("tgop: config: %s" % exc, file=sys.stderr)
            return EXIT_CONFIG
        ok, message = runtime_service.install_service(
            sys.executable, root, config_path=config_path,
            reconcile=namespace.reconcile,
        )
    elif action == "uninstall":
        ok, message = runtime_service.uninstall_service()
    elif action == "start":
        ok, message = runtime_service.start_service()
    elif action == "stop":
        ok, message = runtime_service.stop_service()
    elif action == "restart":
        ok, message = runtime_service.restart_service()
    elif action == "status":
        report = runtime_service.service_status(config_path=config_path)
        sys.stdout.write(runtime_service.render_status_text(report))
        return EXIT_OK
    elif action == "doctor":
        report = runtime_service.doctor(config_path=config_path)
        sys.stdout.write(runtime_service.render_doctor_text(report))
        return EXIT_OK
    else:  # unreachable: argparse restricts the choices
        return EXIT_CONFIG
    print("tgop: runtime-service: %s" % message, file=sys.stderr)
    return EXIT_OK if ok else EXIT_AGENT


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
    if namespace.command == "migrate-state":
        return _migrate_state(namespace)
    if namespace.command == "migrate-workflows":
        return _migrate_workflows(namespace)
    if namespace.command == "runtime-service":
        return _runtime_service(namespace)
    parser.print_help(sys.stderr)
    return EXIT_CONFIG
