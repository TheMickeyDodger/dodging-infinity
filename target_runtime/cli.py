"""Terminal CLI for the DI-REMOTE-2 Runtime (``dirun``).

Subcommands: ``once`` (one claim pass) and ``run`` (poll loop). The
Runtime holds its single-instance lock (the same file /status probes)
for its whole life, constructs the REAL git transport and the REAL
role-turn runner unconditionally — there is NO transport override of
any kind: no environment variable, no CLI flag, no config key (the
static suite proves it) — and takes the control repository path and
state directory from ONE configuration, never two:

- ``--config PATH``: the Telegram adapter's protected configuration,
  UNCHANGED. Such an explicit run is self-contained (Claude trust is
  written beside its state), so a dispatch is refused when the started
  Herdr would read another configuration
  (``RuntimeWorker.workspace_trust_consumable``).
- neither option: the adapter's default configuration file and default
  state directory, UNCHANGED; Claude trust is written to the user-global
  file the started Herdr reads, so a dispatch's trust is consumable.
- ``--runtime-config PATH`` (I3 of task 20261010-164104-1761f6): the
  Runtime's OWN configuration (``target_runtime.runtime_config``), for an
  operator without Telegram. It binds the explicitly configured workflow
  store and establishes Claude trust where the started Herdr actually
  reads it (the same location as the default production route).

Exit codes: 0 success, 2 configuration problem, 3 another Runtime
instance already holds the lock.

MissionCore mode (item 4, the DI World request panel). Naming the panel's four
locations selects a second, separate mode BEFORE any workflow machinery is
built; the default mode above is unchanged and none of its code runs in this
one:

- ``--mission-state-dir`` / ``--mission-control-repo`` /
  ``--mission-workspace-repository`` / ``--mission-workspaces-root``, all four
  together, exactly the locations ``dimission_panel.py`` is started with. The
  stack is composed by ``mission_panel.composition`` (the call the panel makes
  too), so the two share stores, bridge and per-Mission run guard.
- It advances ONLY the requests the panel's own journal durably submitted that
  are approved and not withdrawn, one ``MissionBridge.advance`` step each per
  pass, at the same pacing as the default mode. It never starts a Mission (a
  Mission begins when the person presses Start), never infers success from
  waiting, and never touches a Mission, request or workflow record that is not
  the panel's.
- Its single-owner lock is ``MISSION_RUNTIME_LOCK_FILE_NAME`` in the state
  directory: a different file from the default mode's, so the two modes share no
  claim or lease state, and two instances over the same state cannot both run.
- It publishes ``mission_progress_status.json`` beside the state after every
  pass, which the panel reads to say whether progression is running.
"""

import argparse
import fcntl
import os
import signal
import sys
import threading
import time

from codex_gateway import role_turn as role_turn_module
from telegram_operator.config import ConfigError, load_config
from telegram_operator.state import (
    RUNTIME_LOCK_FILE_NAME,
    default_state_dir,
)

# ``runtime_module`` stays bound here even though this module no longer
# calls it directly: it is the one module object the seam adapter
# resolves its functions from at call time, and the CLI tests replace
# those functions through this name (``cli.runtime_module.<fn>``).
from target_runtime import runtime as runtime_module
from target_runtime import runtime_config as runtime_config_module
from target_runtime import workspace_ownership
from target_runtime.broker import TargetBroker
from target_runtime.durable_execution import RuntimeDurableExecution
from target_runtime.git_transport import GitTransport
from target_runtime.worker import _production_live_workspaces
from target_runtime.workspace import default_workspaces_root
from target_runtime.workspace_trust import default_config_path

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_LOCKED = 3

# MissionCore mode's own single-owner lock, in the panel's state directory. It
# is NOT the default mode's lock (RUNTIME_LOCK_FILE_NAME): the two modes share no
# claim or lease state.
MISSION_RUNTIME_LOCK_FILE_NAME = "mission_runtime.lock"

# Bounded client-side wait between claim passes (ruling N-2: bounded
# waits where genuinely needed; this is pacing, not a mission
# timeout — nothing is ever cancelled by it).
RUNTIME_POLL_INTERVAL_SECONDS = 5


def _refusal_signatures(processed):
    """Bounded, deterministic Runtime refusals from one full pass.

    ``process_once`` intentionally returns every Broker outcome.  The
    production loop used to discard that return value, which made a
    common-gate refusal indistinguishable from a healthy wait.  Keep
    the workflow record as the durable source of truth and surface any
    refusal the action could not record itself in the Runtime log.
    """
    signatures = set()
    for workflow_id in sorted(processed):
        for label, outcome in processed[workflow_id]:
            if outcome.ok:
                continue
            signatures.add((
                workflow_id,
                label,
                outcome.problem or "unspecified_refusal",
                (outcome.detail or "")[:2000],
            ))
    return signatures


def _safe_report_text(value, limit=2000):
    """Bounded text for a value this module did not construct.

    NEVER raises: the refusal fields come from Broker outcomes, so
    rendering them is not this function's code and must not be trusted
    to succeed.
    """
    try:
        return str(value)[:limit]
    except Exception:
        try:
            return "<unprintable %s>" % type(value).__name__
        except Exception:
            return "<unprintable>"


def _report_new_refusals(processed, previously_reported=None):
    """Write each currently persistent refusal once; return the set of
    signatures that have ACTUALLY BEEN REPORTED.

    CONTAINMENT (R-12). This runs inside the unattended `run` loop,
    whose enclosing handler catches only KeyboardInterrupt, so an
    escaping exception kills the Runtime outright. A `BrokenPipeError`
    from the stderr write did exactly that: the loop died after 0 of 3
    polls. It is the same failure class as R-11 in `runtime.py` — a
    DIAGNOSTIC on the DEGRADED path taking down the process it was
    added to make observable — and it is worse here because a healthy
    pass writes nothing and survives a closed stderr, so the runtime
    dies on the FIRST refusal it tries to report. For this mission
    that first refusal is very likely the drift-stranded workflow the
    task was opened for.

    So the write is contained, every rendered value goes through
    `_safe_report_text`, and a missing `sys.stderr` is handled rather
    than left to `print(file=None)`, which would silently redirect the
    diagnostic to stdout.

    WHAT IS DELIBERATELY *NOT* DONE: the whole body is not wrapped in
    a bare `try/except: pass`. That would contain the crash while
    re-introducing the blindness this function exists to remove — the
    normal path must still write, and it is tested that it does.

    SUPPRESSION CORRECTNESS, WHICH IS THE SUBTLE HALF. The caller
    feeds the return value back as `previously_reported` to suppress
    repeats. A signature therefore enters the returned set ONLY IF ITS
    WRITE ACTUALLY SUCCEEDED. Returning `current` wholesale — as this
    function used to — would mark a refusal reported even when its
    write failed, suppressing it FOREVER: after stderr recovered it
    would never be surfaced again. That is permanent blindness, which
    is strictly worse than the crash being fixed. Signatures whose
    write failed stay OUT of the set and remain eligible on the next
    poll. Signatures already reported and still present stay
    suppressed; signatures that have cleared drop out, so a later
    recurrence is surfaced again.
    """
    previous = previously_reported or set()
    current = _refusal_signatures(processed)
    # Already reported AND still present: stays suppressed. A cleared
    # refusal is simply absent from `current`, so it leaves the set.
    reported = current & previous
    stream = getattr(sys, "stderr", None)
    for signature in sorted(current - previous):
        if stream is None:
            # Nothing to report to. NOT reported, so still eligible.
            continue
        workflow_id, label, problem, detail = signature
        suffix = (
            ": %s" % _safe_report_text(detail) if detail else ""
        )
        line = "dirun: workflow %s action %s REFUSED (%s)%s\n" % (
            _safe_report_text(workflow_id, 200),
            _safe_report_text(label, 200),
            _safe_report_text(problem, 200),
            suffix,
        )
        try:
            stream.write(line)
        except Exception:
            # The write FAILED. Do NOT record it as reported: leaving
            # it eligible is what allows it to be surfaced once the
            # stream recovers.
            continue
        reported.add(signature)
    return reported


def acquire_runtime_lock(state_directory, lock_file_name=RUNTIME_LOCK_FILE_NAME):
    """Hold the Runtime's single-instance lock, or return None.

    By default this is the exact lock /status probes: holding it is what
    makes the Runtime visible as running. MissionCore mode passes its own
    file name and so holds a different lock.
    """
    os.makedirs(state_directory, mode=0o700, exist_ok=True)
    lock_path = os.path.join(state_directory, lock_file_name)
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
    parser.add_argument(
        "--runtime-config",
        metavar="PATH",
        default=None,
        help="the Runtime's own configuration (schema %s), in the"
        " workflow store it binds; for an operator without Telegram."
        " Never together with --config"
        % runtime_config_module.RUNTIME_CONFIG_SCHEMA,
    )
    for flag, what in (
        ("--mission-state-dir", "the request panel's state directory"),
        ("--mission-control-repo", "the control repository"),
        ("--mission-workspace-repository", "the repository Mission workspaces"
                                           " are prepared from"),
        ("--mission-workspaces-root", "the directory Mission workspaces are"
                                      " prepared under"),
    ):
        parser.add_argument(
            flag,
            metavar="PATH",
            default=None,
            help="MissionCore mode (all four together, absolute paths): %s,"
            " exactly as dimission_panel.py is started. Advances the"
            " Missions that panel started. Never with --config or"
            " --runtime-config" % what,
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
    if namespace.config is not None and namespace.runtime_config is not None:
        # Refused before EITHER loader runs: which schema to read is never
        # guessed, and one is never tried after the other.
        raise runtime_config_module.RuntimeConfigError(
            runtime_config_module.PROBLEM_AMBIGUOUS,
            "--config (the Telegram adapter configuration) and"
            " --runtime-config (the Runtime's own configuration) were both"
            " given; name exactly one")
    # The Claude configuration the started Herdr READS: the user-global
    # file under the live HOME (RuntimeWorker.workspace_trust_consumable).
    consumed_trust_config = default_config_path()
    if namespace.runtime_config is not None:
        # I3: the Runtime's own configuration. It binds the store it names
        # (verified to be its own directory) and writes Claude trust where
        # the started Herdr reads it, exactly as the default route does,
        # so its dispatch is consumable. The adapter's loader is not run.
        selected = runtime_config_module.load_runtime_config(
            namespace.runtime_config)
        return _composed_broker(
            selected.workflow_store, selected.repository,
            os.path.join(selected.workflow_store, "workspaces"),
            consumed_trust_config)
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
    # The trust-establishment target travels with the workspaces
    # root: an injected --config run is self-contained (its own
    # state directory, its own workspaces, its own Claude config), so
    # within such a run the developer's real ~/.claude.json is not
    # the write target. Outside that, and enforced separately: such a
    # run is refused at dispatch, because the child would read a
    # different configuration (see
    # RuntimeWorker.workspace_trust_consumable).
    claude_config_path = os.path.join(
        state_directory, ".claude.json"
    ) if config_path else consumed_trust_config
    return _composed_broker(state_directory, loaded.repository,
                            workspaces_root, claude_config_path)


def _composed_broker(state_directory, control_repository, workspaces_root,
                     claude_config_path):
    """The ONE production Broker composition both configurations use."""
    broker = TargetBroker(
        store_directory=state_directory,
        control_repository_realpath=control_repository,
        transport=GitTransport(),
        workspaces_root=workspaces_root,
        role_turn_fn=production_role_turn,
        claude_config_path=claude_config_path,
        # R-31 W-1: DOMAIN B's two capabilities are supplied HERE,
        # deliberately and by name, at the one production boundary.
        #
        # The Broker's defaults stay None, and that is correct: "no
        # capability" is the right fail-closed default, and a default
        # that reached a live workspace close would be the opposite of
        # what `workspace_ownership` argues for. # The defect the defaults left was that production did not hand
        # the capability over at all, so within terminal cleanup the
        # only reachable outcome was degraded. It is handed over exactly here.
        #
        # `production_close` runs `herdr workspace close` and is
        # reached ONLY through an OWNED verdict from
        # `workspace_ownership.close_owned_workspace`, which requires
        # exact and unique agreement between the workflow record, one
        # child record and one live workspace whose agent names match
        # the recorded ones. On a machine carrying many workspaces of
        # which one is ours, that chain is what stands between cleanup
        # and destroying somebody else's live sessions.
        live_workspaces_fn=_production_live_workspaces,
        workspace_close_fn=workspace_ownership.production_close,
    )
    return broker, state_directory


_MISSION_OPTIONS = ("mission_state_dir", "mission_control_repo",
                    "mission_workspace_repository", "mission_workspaces_root")
_MISSION_FLAGS = ("--mission-state-dir", "--mission-control-repo",
                  "--mission-workspace-repository", "--mission-workspaces-root")
# What a pass can report that a person needs to know about and that no record of
# the Mission says on its own: reported once, again if it clears and recurs.
_MISSION_NOTABLE_ACTIONS = ("hold", "refused", "unreadable")


def _mission_locations(namespace):
    """The four locations, validated, or ``ValueError`` with why not."""
    if namespace.config is not None or namespace.runtime_config is not None:
        raise ValueError(
            "MissionCore mode never combines with --config or"
            " --runtime-config: it composes from the request panel's own"
            " locations")
    values = [getattr(namespace, name) for name in _MISSION_OPTIONS]
    missing = [flag for flag, value in zip(_MISSION_FLAGS, values) if value is None]
    if missing:
        raise ValueError(
            "MissionCore mode needs all four locations together; missing: %s"
            % ", ".join(missing))
    for flag, value in zip(_MISSION_FLAGS, values):
        if not os.path.isabs(value):
            raise ValueError("%s must be an absolute path" % flag)
    state_directory, control, repository, root = values
    if not os.path.isdir(control):
        raise ValueError("the control repository is not a directory")
    if not os.path.isdir(repository):
        raise ValueError("the workspace repository is not a directory")
    return state_directory, control, repository, root


def _report_mission_conditions(report, previously_reported):
    """Write each notable condition once; return those ACTUALLY reported.

    The same containment as ``_report_new_refusals``: a diagnostic on the
    degraded path must never end the unattended loop (a closed stderr did
    exactly that to the default mode), and a condition whose write failed stays
    eligible for the next pass."""
    current = {
        (m["mission_id"], m["action"], m["waiting_for"]): m
        for m in report["missions"] if m["action"] in _MISSION_NOTABLE_ACTIONS}
    reported = set(current) & set(previously_reported)
    stream = getattr(sys, "stderr", None)
    for key in sorted(set(current) - set(previously_reported), key=repr):
        if stream is None:
            continue
        entry = current[key]
        line = "dirun: mission %s %s (%s): %s\n" % (
            _safe_report_text(entry["mission_id"], 200),
            _safe_report_text(entry["action"], 200),
            _safe_report_text(entry["waiting_for"], 200),
            _safe_report_text(entry["detail"]))
        try:
            stream.write(line)
        except Exception:
            continue
        reported.add(key)
    return reported


def _say(line):
    """One diagnostic line to stderr, contained (see the function above)."""
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return False
    try:
        stream.write(line + "\n")
    except Exception:
        return False
    return True


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _main_mission(namespace, sleeper, passes, composition_factory):
    """MissionCore mode: pass over the panel's started Missions, publishing a
    status after every pass. Exit codes as the default mode's."""
    try:
        state_directory, control, repository, root = _mission_locations(namespace)
    except ValueError as exc:
        print("dirun: config: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    lock_descriptor = acquire_runtime_lock(
        state_directory, MISSION_RUNTIME_LOCK_FILE_NAME)
    if lock_descriptor is None:
        print(
            "dirun: another MissionCore runtime already holds the lock in %s;"
            " refusing to run twice" % state_directory,
            file=sys.stderr,
        )
        return EXIT_LOCKED
    # Imported here, not at module level: the default mode never loads them.
    from mission_panel import composition as composition_module
    from mission_panel import progress as progress_module
    from target_runtime import mission_progress_status as status_module
    previous_handler = None
    if namespace.command == "run" and (
        threading.current_thread() is threading.main_thread()
    ):
        previous_handler = signal.signal(signal.SIGTERM, _raise_interrupt)
    started_at = int(time.time())
    done = 0
    report = {"examined": 0, "eligible": 0, "truncated": False,
              "unknown_requests": 0, "missions": []}
    problem = None
    digest = None
    try:
        try:
            composed = (composition_factory or composition_module.compose)(
                state_directory, control, repository, root)
            digest = composed.digest
            if composed.bridge is None:
                raise ValueError("no bridge")
            ledger = progress_module.ProgressionPass(
                composed.journal, composed.surface, composed.bridge)
        except Exception as exc:
            print("dirun: config: cannot compose the Mission stack (%s)"
                  % type(exc).__name__, file=sys.stderr)
            return EXIT_CONFIG
        pause = sleeper or time.sleep
        remaining = passes
        reported = set()
        reported_problem = None
        reported_publish = None
        while True:
            try:
                report = ledger.run_pass()
                problem = None
            except Exception as exc:
                # The pass could not read its records AT ALL: published as that
                # (never as a quiet "nothing to do") and shown once.
                report = {"examined": 0, "eligible": 0, "truncated": False,
                          "unknown_requests": 0, "missions": []}
                problem = type(exc).__name__
            done += 1
            if problem != reported_problem:
                if problem is not None:
                    _say("dirun: mission progression pass failed (%s); nothing"
                         " was advanced" % problem)
                reported_problem = problem
            reported = _report_mission_conditions(report, reported)
            try:
                status_module.write_status(
                    state_directory, digest, os.getpid(), started_at, done,
                    RUNTIME_POLL_INTERVAL_SECONDS, report, False,
                    int(time.time()), problem)
                reported_publish = None
            except OSError as exc:
                if reported_publish != type(exc).__name__:
                    _say("dirun: the status file could not be written (%s)"
                         % type(exc).__name__)
                reported_publish = type(exc).__name__
            if namespace.command == "once":
                _say("dirun: mission progression pass: examined %d of %d"
                     " eligible request(s); %d linked request(s) unknown to"
                     " the store" % (report["examined"], report["eligible"],
                                     report["unknown_requests"]))
                return EXIT_OK
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    return EXIT_OK
            pause(RUNTIME_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
        if digest is not None:
            try:
                status_module.write_status(
                    state_directory, digest, os.getpid(), started_at, done,
                    RUNTIME_POLL_INTERVAL_SECONDS, report, True,
                    int(time.time()), problem)
            except OSError:
                pass
        os.close(lock_descriptor)


def main(argv=None, sleeper=None, passes=None, execution=None,
         mission_composition=None):
    """CLI entry. ``sleeper``/``passes`` exist ONLY for tests (an
    injected pacing bound); production callers pass nothing and run
    unbounded — pacing, not a mission timeout. ``execution`` likewise
    exists ONLY for tests: a ``DurableExecution`` that replaces the
    three Runtime calls and nothing else (config load, lock, exit
    codes, printing, and pacing are the real code either way);
    production callers pass nothing and get the Runtime-backed adapter
    built from ``_build_broker``. ``mission_composition`` is likewise
    test-only: it replaces ``mission_panel.composition.compose`` in
    MissionCore mode (a hermetic test substitutes the bridge's two effect
    seams through it); production passes nothing."""
    parser = _build_parser()
    try:
        namespace = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG
    if namespace.command not in ("once", "run"):
        parser.print_help(sys.stderr)
        return EXIT_CONFIG
    if any(getattr(namespace, name) is not None for name in _MISSION_OPTIONS):
        # Selected BEFORE the workflow store, broker or recovery path exists.
        return _main_mission(namespace, sleeper, passes, mission_composition)
    try:
        broker, state_directory = _build_broker(namespace)
    except ConfigError as exc:
        print("dirun: config: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    if execution is None:
        execution = RuntimeDurableExecution(broker, state_directory)
    lock_descriptor = acquire_runtime_lock(state_directory)
    if lock_descriptor is None:
        print(
            "dirun: another Runtime instance already holds the lock"
            " in %s; refusing to run twice" % state_directory,
            file=sys.stderr,
        )
        return EXIT_LOCKED
    # R-34 Z-3: RESTART RECOVERY, on the production entry point.
    #
    # It is here now because the precondition V-2 named is met:
    # production REGISTERS every owned spawn under a scope naming its
    # workflow and task, so this enumerates records production wrote
    # and attributes each to its owner BEFORE acting. The unscoped
    # global sweep that briefly sat here did neither, which is why it
    # was removed rather than kept.
    #
    # Placed after the Runtime lock, so exactly one Runtime does it,
    # and BEFORE a workflow is advanced: advancing first would mean
    # working alongside a previous run's processes.
    #
    # # Unattributed directories are REPORTED and left alone — WITH THE
    # REASON, so a forged assignment is distinguishable in the log
    # from a stray directory (R-43 AG-2).
    recovered_rows, unattributed = execution.recover_inherited_processes()
    for row in recovered_rows:
        identity, reaped, stuck, unstamped, uncorroborated = row
        print(
            "dirun: inherited processes for %s %s/%s (control %s):"
            " reaped %d, stuck %d, unstamped %d, uncorroborated %d"
            % (identity.owner_type, identity.owner_id,
               identity.unit_id, identity.control_digest,
               len(reaped), len(stuck), len(unstamped),
               len(uncorroborated)),
            file=sys.stderr,
        )
        # R-54 AR-3: an unproven LIVE group is named, within this
        # line, with its REASON. Within a receipt
        # "uncorroborated 1" leaves an operator unable to act; "group
        # 44603 has been REUSED" does not, and that is the difference
        # between a number and a process.
        for directory, pgid, reason in uncorroborated:
            print(
                "dirun: group %d under %s is REPORTED and left alone"
                " (%s)" % (pgid, directory, reason),
                file=sys.stderr,
            )
    for directory, reason in unattributed:
        print(
            "dirun: unattributed process record directory REPORTED and"
            " left alone (%s): %s" % (reason, directory),
            file=sys.stderr,
        )
    # R-12 CONDITION: BOOTSTRAP_UNOBSERVABLE is SURFACED, not merely
    # stored. It is deliberately not a stopping state — bounding on
    # absence of evidence would expire a mission that is only
    # unreadable — but until this line it lived exclusively in a
    # receipt, which is silent in the one sense that matters.
    #
    # The DENOMINATOR is printed even when no row needs attention, so
    # an empty result reads as "the enumeration ran and found none"
    # rather than leaving a reader to wonder whether it ran at all.
    attention, total = execution.readiness_attention()
    if total is None:
        print(
            "dirun: readiness could not be enumerated (store"
            " unreadable); this is an absent MEASUREMENT, not an"
            " absence of problems",
            file=sys.stderr,
        )
    else:
        print(
            "dirun: readiness attention: %d of %d workflow(s)"
            % (len(attention), total),
            file=sys.stderr,
        )
        for row in attention:
            print(
                "dirun:   %s is %s in phase %s (%s)"
                % (row["workflow_id"], row["state"], row["phase"],
                   "STOPS the workflow" if row["stops_the_workflow"]
                   else "does NOT stop the workflow; recorded and"
                        " surfaced so the wait is not silent"),
                file=sys.stderr,
            )
    try:
        if namespace.command == "once":
            processed = execution.process_once()
            _report_new_refusals(processed)
            print(
                "dirun: processed %d workflow(s) (exact)"
                % len(processed),
                file=sys.stderr,
            )
            return EXIT_OK
        pause = sleeper or time.sleep
        remaining = passes
        reported_refusals = set()
        while True:
            processed = execution.process_once()
            reported_refusals = _report_new_refusals(
                processed, reported_refusals
            )
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    return EXIT_OK
            pause(RUNTIME_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        return EXIT_OK
    finally:
        os.close(lock_descriptor)
