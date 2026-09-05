"""Per-user macOS LaunchAgent supervision for the DI Runtime (``dirun run``).

The Runtime is the long-lived process that claims durably authorized
missions. This module is its supervision adapter: it generates the
LaunchAgent job definition, installs / reconciles / uninstalls it
under the user's OWN ``gui/<uid>`` launchd domain, starts / stops /
restarts it through fully-qualified ``launchctl`` verbs, and OBSERVES
its health (``status`` / ``doctor``) without ever equating plist
existence with a running Runtime.

Authority boundary: the single-instance authority is, and remains,
``acquire_runtime_lock`` in the Runtime itself (a ``flock`` on
``<state dir>/runtime.lock`` held for the process lifetime). This
layer never re-implements that lock and never adds a second one; it
only probes it, through the adapter's existing non-destructive probe.

The job definition binds an ABSOLUTE interpreter and an ABSOLUTE
stable code path (``<root>/dirun.py``), sets no working directory,
passes no interactive-shell environment, and logs to durable files in
the protected state directory OUTSIDE any Git-tracked path. Install
REFUSES an unstable code location (a temp path, a git worktree whose
``.git`` is a file, a symlinked root, a world-writable directory), and
REFUSES to replace a differing or unparseable existing job under the
same label unless explicitly told to reconcile.

Lifecycle boundary, stated rather than hidden: a ``gui/<uid>``
LaunchAgent is bootstrapped when the user's GUI session is
established. After a reboot the Runtime starts once that user logs
in; it does not start before login. That is the supported model for
a process that runs user-owned workspaces from a per-user state
directory.

Import-safe on any platform: tests inject the ``launchctl`` runner
and the binary resolver; only production code paths ever reach the
real host.
"""

import os
import plistlib
import shutil
import stat
import subprocess

from telegram_operator import host_readiness
from telegram_operator.config import default_config_path, load_config, ConfigError
from telegram_operator.launchagent import agent_state_dir
from telegram_operator.mission import runtime_status
from telegram_operator.state import RUNTIME_LOCK_FILE_NAME

SERVICE_LABEL = "com.dodginginfinity.dirun"
# Minimum seconds launchd waits before relaunching after an exit.
THROTTLE_SECONDS = 10
# Seconds launchd waits after SIGTERM before SIGKILL on a requested
# stop: the explicit bound on graceful termination (launchd's own
# default would be 5 s, inherited silently; this makes the bound ours).
EXIT_TIMEOUT_SECONDS = 30
# Fixed base PATH for the launchd job. launchd does NOT inherit the
# interactive shell PATH; the install-time resolution of the codex
# binary is prepended and nothing else from the environment passes
# through.
SERVICE_BASE_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CODEX_BINARY_NAME = "codex"
ENTRY_SCRIPT_NAME = "dirun.py"
STDOUT_LOG_NAME = "dirun.out.log"
STDERR_LOG_NAME = "dirun.err.log"
# Roots under these prefixes are deletable / ephemeral and are never
# an acceptable code path for an always-on service.
DEFAULT_TEMP_PREFIXES = (
    "/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
    "/var/folders", "/private/var/folders",
)

STATE_NOT_INSTALLED = "not_installed"
STATE_FOREIGN = "foreign"
STATE_INSTALLED_STOPPED = "installed_stopped"
STATE_RUNNING = "running"
STATE_READY = "ready"
STATE_DEGRADED = "degraded"
STATE_UNOBSERVABLE = "unobservable"

# The Runtime's own refusal exit code when another instance holds the
# lock (target_runtime.cli.EXIT_LOCKED). Named here as a VALUE only:
# this package must never import the Runtime.
RUNTIME_LOCKED_EXIT_CODE = 3

GUI_LOGIN_LIMITATION = (
    "Startup boundary: this is a per-user LaunchAgent in the gui/<uid>"
    " domain. It starts when the user's GUI session is established,"
    " i.e. after a reboot it starts once that user logs in (automatic"
    " login is a host-policy decision outside this tool); it does not"
    " start before login. A stop lasts until an explicit start or the"
    " next user-session boundary; uninstall is the permanent form."
)
TAILNET_POLICY_LIMITATION = host_readiness.TAILNET_POLICY_STATEMENT

# A stop is TRANSIENT and that is reported, never hidden: whenever the
# state is installed_stopped these three facts are in the detail (and so
# in status and doctor text). Stable substrings, pinned by a test.
STOPPED_PLIST_INSTALLED_NOTE = "the job definition is still installed"
STOPPED_RELAUNCH_NOTE = (
    "launchd will start the job again at the next user-session boundary"
)
STOPPED_UNINSTALL_NOTE = "uninstall is the permanent form"
STOPPED_TRANSIENCE_NOTE = "%s; %s; %s" % (
    STOPPED_PLIST_INSTALLED_NOTE, STOPPED_RELAUNCH_NOTE,
    STOPPED_UNINSTALL_NOTE,
)


class ServiceError(Exception):
    """A launchctl observation could not be interpreted."""


def _default_runner(argv):
    """Run ``argv`` and return ``(returncode, stdout, stderr)``."""
    completed = subprocess.run(argv, capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


# -- paths and the job definition ---------------------------------------

def service_plist_path(home=None):
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(
        base, "Library", "LaunchAgents", "%s.plist" % SERVICE_LABEL
    )


def service_state_dir(home=None, config_path=None):
    """The directory the job logs into and the Runtime keeps its lock
    in: the ``--config`` directory when one was named, otherwise the
    default protected state directory. The SAME derivation the tgop
    LaunchAgent uses, so logs, state and lock never split."""
    return agent_state_dir(home, config_path)


def service_log_paths(home=None, config_path=None):
    state_dir = service_state_dir(home, config_path)
    return (
        os.path.join(state_dir, STDOUT_LOG_NAME),
        os.path.join(state_dir, STDERR_LOG_NAME),
    )


def service_domain():
    return "gui/%d" % os.getuid()


def service_target():
    """The fully-qualified launchctl service target of the owned job."""
    return "%s/%s" % (service_domain(), SERVICE_LABEL)


def unstable_root_reason(root, temp_prefixes=DEFAULT_TEMP_PREFIXES):
    """None when ``root`` is an acceptable stable code path, else a
    human-readable reason to refuse it. ``temp_prefixes`` exists only
    so the accept path is testable from a temp directory; production
    always uses the default tuple."""
    if not isinstance(root, str) or not root:
        return "code path is empty"
    if not os.path.isabs(root):
        return "code path %r is not absolute" % root
    absolute = os.path.abspath(root)
    if not os.path.exists(absolute):
        return "code path %s does not exist" % absolute
    real = os.path.realpath(absolute)
    if os.path.islink(absolute) or real != absolute:
        return (
            "code path %s resolves through a symlink to %s; the service"
            " must bind one unambiguous location" % (absolute, real)
        )
    if not os.path.isdir(absolute):
        return "code path %s is not a real directory" % absolute
    entry = os.path.join(absolute, ENTRY_SCRIPT_NAME)
    if os.path.islink(entry) or not os.path.isfile(entry):
        return "code path %s has no regular %s" % (absolute, ENTRY_SCRIPT_NAME)
    git_marker = os.path.join(absolute, ".git")
    if os.path.lexists(git_marker) and not os.path.isdir(git_marker):
        return (
            "code path %s is a git worktree or submodule (.git is a"
            " file, not a directory); a worktree can be removed while"
            " the service definition survives" % absolute
        )
    for prefix in temp_prefixes:
        if absolute == prefix or absolute.startswith(prefix.rstrip("/") + "/"):
            return (
                "code path %s is under the temporary prefix %s; an"
                " always-on service must bind a durable location"
                % (absolute, prefix)
            )
    mode = os.stat(absolute).st_mode
    if mode & stat.S_IWOTH:
        return "code path %s is world-writable (mode %o)" % (
            absolute, stat.S_IMODE(mode),
        )
    return None


def _job_path(codex_directory):
    if not codex_directory:
        return SERVICE_BASE_PATH
    return ":".join(
        [codex_directory] + [
            directory
            for directory in SERVICE_BASE_PATH.split(":")
            if directory != codex_directory
        ]
    )


def build_service_plist(python_executable, root, home=None,
                        config_path=None, codex_directory=None):
    """The LaunchAgent job dictionary: absolute paths only, no working
    directory, no environment beyond a fixed PATH, explicit startup,
    restart and stop-bound semantics, logs in the state directory."""
    stdout_log, stderr_log = service_log_paths(home, config_path)
    program_arguments = [
        os.path.abspath(python_executable),
        os.path.join(os.path.abspath(root), ENTRY_SCRIPT_NAME),
    ]
    if config_path is not None:
        program_arguments += ["--config", os.path.abspath(config_path)]
    program_arguments.append("run")
    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": program_arguments,
        "EnvironmentVariables": {"PATH": _job_path(codex_directory)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": THROTTLE_SECONDS,
        "ExitTimeOut": EXIT_TIMEOUT_SECONDS,
        "StandardOutPath": stdout_log,
        "StandardErrorPath": stderr_log,
    }


def _load_existing_plist(path):
    """``(parsed_dict_or_None, error_or_None)`` for an existing plist.

    A file that does not parse, AND a file that parses to something
    other than a dictionary (an array, a string, an integer), are both
    the ambiguous class: neither is a job definition, so the caller
    refuses (install) or reports ``foreign`` (status) without ever
    assuming a dict.
    """
    try:
        with open(path, "rb") as handle:
            parsed = plistlib.load(handle)
    except Exception as exc:  # any parse failure is "ambiguous"
        return None, "%s: %s" % (type(exc).__name__, exc)
    if not isinstance(parsed, dict):
        return None, "plist root is %s, not a dictionary" % (
            type(parsed).__name__,
        )
    return parsed, None


def _owned_definition(parsed):
    """True when ``parsed`` is a dirun Runtime job under our label:
    the label matches and ProgramArguments has the exact shape
    ``[python, <root>/dirun.py, (--config, PATH)?, run]``."""
    if not isinstance(parsed, dict):
        return False
    if parsed.get("Label") != SERVICE_LABEL:
        return False
    arguments = parsed.get("ProgramArguments")
    if not isinstance(arguments, list) or len(arguments) not in (3, 5):
        return False
    if not all(isinstance(item, str) for item in arguments):
        return False
    if os.path.basename(arguments[1]) != ENTRY_SCRIPT_NAME:
        return False
    if arguments[-1] != "run":
        return False
    if len(arguments) == 5 and arguments[2] != "--config":
        return False
    return True


# -- launchctl observation ----------------------------------------------

def parse_launchctl_list(text):
    """Parse the scalar lines of ``launchctl list <label>`` output.

    Returns a dict of the scalar keys (strings, integers, booleans) or
    None when the text is not that dictionary shape. Nested arrays and
    dictionaries are skipped, never guessed at.
    """
    if not isinstance(text, str) or "{" not in text:
        return None
    parsed = {}
    depth = 0
    saw_open = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "{":
            saw_open = True
            depth += 1
            continue
        if line.startswith("}"):
            depth -= 1
            continue
        if depth != 1:
            # Inside a nested array/dict (or before the opening
            # brace): scalar lines here are not top-level fields.
            if line.endswith("= (") or line.endswith("= {"):
                depth += 1
            elif line.startswith(")") :
                depth -= 1
            continue
        if line.endswith("= (") or line.endswith("= {"):
            depth += 1
            continue
        if not line.startswith('"') or " = " not in line or not line.endswith(";"):
            return None
        key_part, value_part = line[:-1].split(" = ", 1)
        if not (key_part.startswith('"') and key_part.endswith('"')):
            return None
        key = key_part[1:-1]
        value = value_part.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            parsed[key] = value[1:-1]
        elif value in ("true", "false"):
            parsed[key] = value == "true"
        else:
            try:
                parsed[key] = int(value)
            except ValueError:
                return None
    if not saw_open or depth != 0 or "Label" not in parsed:
        return None
    return parsed


def _decode_exit_status(raw):
    """launchd reports the previous instance's wait status; ``768`` is
    exit code 3. Small raw values are taken as exit codes directly."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return None
    if raw >= 256:
        return raw >> 8
    return raw


def _observe_job(run):
    """Observe the owned label through ``launchctl list <label>``.

    Returns ``(bootstrapped, parsed, detail)`` where ``bootstrapped``
    is True (job present, ``parsed`` is its dictionary), False (label
    not in the domain), or None (unobservable, ``detail`` says why).
    """
    argv = ["launchctl", "list", SERVICE_LABEL]
    try:
        returncode, stdout, stderr = run(argv)
    except Exception as exc:
        return None, None, "launchctl could not be run (%s: %s)" % (
            type(exc).__name__, exc,
        )
    if returncode == 0:
        parsed = parse_launchctl_list(stdout)
        if parsed is None or parsed.get("Label") != SERVICE_LABEL:
            return None, None, (
                "launchctl list output for %s did not parse"
                % SERVICE_LABEL
            )
        return True, parsed, "bootstrapped in %s" % service_domain()
    text = "%s %s" % (stdout or "", stderr or "")
    if returncode == 113 or "Could not find service" in text:
        return False, None, "%s is not bootstrapped in %s" % (
            SERVICE_LABEL, service_domain(),
        )
    return None, None, (
        "launchctl list %s exited %d unexpectedly: %s" % (
            SERVICE_LABEL, returncode, text.strip()[:500],
        )
    )


def _prepare_state_dir(state_dir):
    """Create the log/state directory private to the user (0700)."""
    os.makedirs(state_dir, mode=0o700, exist_ok=True)


def _bootstrap(run, plist_path):
    return run(["launchctl", "bootstrap", service_domain(), plist_path])


def _bootout(run):
    """One ``launchctl bootout`` of the owned target.

    Returns ``(outcome, detail)`` with outcome ``"stopped"`` (exit 0),
    ``"not_loaded"`` (the label was not bootstrapped: launchd's
    "No such process" / "Could not find service" refusal, tolerated),
    or ``"failed"``. Never ``disable``: the per-user disabled database
    is root-owned, keyed by our label, and survives plist deletion, so
    a leaked override could stop the real Runtime from starting at the
    next login.
    """
    try:
        code, stdout, stderr = run(["launchctl", "bootout", service_target()])
    except Exception as exc:
        return "failed", "launchctl could not be run (%s: %s)" % (
            type(exc).__name__, exc,
        )
    if code == 0:
        return "stopped", "booted out %s" % service_target()
    text = "%s %s" % (stdout or "", stderr or "")
    not_loaded_text = "No such process" in text or (
        "Could not find service" in text
    )
    # Fail CLOSED: an exit 3/113 is "not loaded" only when launchd's
    # own not-loaded text is present, or when there is no text at all.
    # Any other message on those codes is an unexplained failure.
    if not_loaded_text or (code in (3, 113) and not text.strip()):
        return "not_loaded", "%s was not bootstrapped" % service_target()
    return "failed", "`launchctl bootout %s` exited %d: %s" % (
        service_target(), code, text.strip()[:500],
    )


# -- install / uninstall ------------------------------------------------

def install_service(python_executable, root, home=None, runner=None,
                    which=None, config_path=None, reconcile=False,
                    temp_prefixes=DEFAULT_TEMP_PREFIXES):
    """Install the Runtime LaunchAgent. Returns ``(ok, message)``.

    Refuses, BEFORE writing anything: an unstable code path; a config
    the job could not run; an unresolvable codex binary; an existing
    differing or unparseable job under the same label (unless
    ``reconcile``). An identical existing job is a no-op that still
    ensures the job is bootstrapped. ``temp_prefixes`` is forwarded to
    ``unstable_root_reason`` and exists only so hermetic tests can
    install from a temp fixture root; production callers never pass it.
    """
    run = runner or _default_runner
    resolve = which or shutil.which
    root = os.path.abspath(root) if isinstance(root, str) and root else root
    reason = unstable_root_reason(root, temp_prefixes=temp_prefixes)
    if reason is not None:
        return False, (
            "refusing to install: %s. Nothing was written. Install from"
            " the stable production checkout (--root PATH)." % reason
        )
    resolved_config = (
        os.path.abspath(config_path) if config_path is not None
        else default_config_path(home)
    )
    try:
        load_config(resolved_config)
    except ConfigError as exc:
        return False, (
            "refusing to install: the job would run against config %s"
            " which is unusable (%s). Nothing was written."
            % (resolved_config, exc)
        )
    codex_path = resolve(CODEX_BINARY_NAME)
    if not codex_path:
        return False, (
            "the %r binary is not resolvable on the current PATH, so"
            " the installed Runtime could never complete a"
            " handoff-validation turn. Nothing was installed. Put"
            " codex on PATH and re-run install." % CODEX_BINARY_NAME
        )
    codex_directory = os.path.dirname(os.path.abspath(codex_path))
    desired = build_service_plist(
        python_executable, root, home, config_path=config_path,
        codex_directory=codex_directory,
    )
    plist_path = service_plist_path(home)
    replaced = None
    if os.path.lexists(plist_path):
        existing, error = _load_existing_plist(plist_path)
        if error is not None:
            if not reconcile:
                return False, (
                    "refusing to install: an existing job definition at"
                    " %s does not parse (%s) and is therefore ambiguous."
                    " Nothing was changed. Re-run with --reconcile to"
                    " replace it explicitly." % (plist_path, error)
                )
            replaced = "an unparseable definition (%s)" % error
        elif existing == desired:
            bootstrapped, _, detail = _observe_job(run)
            if bootstrapped is None:
                return False, (
                    "already installed, unchanged at %s; but its"
                    " bootstrap state is unobservable: %s"
                    % (plist_path, detail)
                )
            if bootstrapped:
                return True, (
                    "already installed, unchanged at %s (bootstrapped)"
                    % plist_path
                )
            code, _, stderr = _bootstrap(run, plist_path)
            if code != 0:
                return False, (
                    "already installed, unchanged at %s; but"
                    " `launchctl bootstrap` exited %d: %s" % (
                        plist_path, code, (stderr or "").strip()[:500],
                    )
                )
            return True, (
                "already installed, unchanged at %s (bootstrapped now)"
                % plist_path
            )
        else:
            differing = sorted(
                key for key in set(existing) | set(desired)
                if existing.get(key) != desired.get(key)
            )
            if not reconcile:
                return False, (
                    "refusing to install: an existing job definition at"
                    " %s DIFFERS from the one this checkout would"
                    " install (differing keys: %s). Nothing was"
                    " changed. Re-run with --reconcile to replace it"
                    " explicitly." % (plist_path, ", ".join(differing))
                )
            replaced = "a differing definition (differing keys: %s)" % (
                ", ".join(differing),
            )
    os.makedirs(os.path.dirname(plist_path), mode=0o755, exist_ok=True)
    # launchd must be able to open the log paths on the very first
    # launch, before the Runtime itself ever created its state dir.
    # (In practice the directory already exists — it is the validated
    # config's own directory, which load_config has just required to
    # carry no group/other bits — so this is defensive.)
    _prepare_state_dir(service_state_dir(home, config_path))
    payload = plistlib.dumps(desired)
    descriptor = os.open(
        plist_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
    os.chmod(plist_path, 0o600)
    if replaced is not None:
        # A reconciled definition replaces a job launchd may still be
        # running under the OLD definition: boot it out first so the
        # bootstrap below loads the new one, never two.
        bootstrapped, _, _ = _observe_job(run)
        if bootstrapped:
            run(["launchctl", "bootout", service_target()])
    code, _, stderr = _bootstrap(run, plist_path)
    if code != 0:
        return False, (
            "wrote %s but `launchctl bootstrap %s` exited %d: %s. Check"
            " `launchctl list %s`" % (
                plist_path, service_domain(), code,
                (stderr or "").strip()[:500], SERVICE_LABEL,
            )
        )
    if replaced is not None:
        return True, "reconciled: replaced %s at %s and bootstrapped it" % (
            replaced, plist_path,
        )
    return True, "installed and bootstrapped %s" % plist_path


def uninstall_service(home=None, runner=None):
    """Boot the job out and remove exactly its plist. ``(ok, message)``.
    Never touches logs, the runtime lock, state, or workspaces."""
    run = runner or _default_runner
    plist_path = service_plist_path(home)
    if not os.path.lexists(plist_path):
        return True, "nothing installed at %s" % plist_path
    outcome, detail = _bootout(run)
    if outcome == "failed":
        return False, (
            "%s; %s left in place so the on-disk definition still"
            " matches the loaded job" % (detail, plist_path)
        )
    os.unlink(plist_path)
    if outcome == "stopped":
        return True, "booted out %s and removed %s" % (
            service_target(), plist_path,
        )
    return True, "removed %s (it was not bootstrapped)" % plist_path


# -- start / stop / restart ---------------------------------------------

def start_service(home=None, runner=None):
    run = runner or _default_runner
    plist_path = service_plist_path(home)
    if not os.path.lexists(plist_path):
        return False, "not installed (no %s); run install first" % plist_path
    bootstrapped, _, detail = _observe_job(run)
    if bootstrapped is None:
        return False, "cannot start: %s" % detail
    # Defensive idempotence against a hand-set disabled override; it
    # creates no state of ours. Its result is REPORTED, never dropped.
    enable_code, _, enable_stderr = run(
        ["launchctl", "enable", service_target()]
    )
    enable_note = ""
    if enable_code != 0:
        enable_note = " (`launchctl enable %s` exited %d: %s)" % (
            service_target(), enable_code,
            (enable_stderr or "").strip()[:200],
        )
    if bootstrapped:
        code, _, stderr = run(["launchctl", "kickstart", service_target()])
        verb = "kickstart"
    else:
        code, _, stderr = _bootstrap(run, plist_path)
        verb = "bootstrap"
    if code != 0:
        return False, "`launchctl %s` for %s exited %d: %s%s" % (
            verb, service_target(), code, (stderr or "").strip()[:500],
            enable_note,
        )
    return True, "started %s (%s)%s" % (service_target(), verb, enable_note)


def stop_service(home=None, runner=None):
    """Bounded graceful stop: exactly one ``launchctl bootout`` of the
    owned target (SIGTERM, then SIGKILL after ExitTimeOut). Durable
    state, workspaces and logs are untouched; ``start`` (or the next
    user session) brings it back. NEVER ``disable``: see ``_bootout``."""
    run = runner or _default_runner
    outcome, detail = _bootout(run)
    if outcome == "failed":
        return False, "cannot stop: %s" % detail
    if outcome == "not_loaded":
        return True, "%s; nothing to stop (%s)" % (
            detail, STOPPED_TRANSIENCE_NOTE,
        )
    return True, (
        "stopped %s (SIGTERM, SIGKILL after %d s); it stays stopped"
        " until `start`, and %s" % (
            service_target(), EXIT_TIMEOUT_SECONDS,
            STOPPED_TRANSIENCE_NOTE,
        )
    )


def restart_service(home=None, runner=None):
    """One atomic ``kickstart -k``: launchd terminates the running
    instance (bounded by ExitTimeOut) and relaunches it; no window
    exists in which two instances are asked to run."""
    run = runner or _default_runner
    code, _, stderr = run(
        ["launchctl", "kickstart", "-k", service_target()]
    )
    if code != 0:
        return False, (
            "`launchctl kickstart -k %s` exited %d: %s (is the job"
            " installed and started?)" % (
                service_target(), code, (stderr or "").strip()[:500],
            )
        )
    return True, "restarted %s (kickstart -k)" % service_target()


# -- status / doctor ----------------------------------------------------

def service_status(home=None, runner=None, config_path=None):
    """Observe the owned job. Returns a plain dict; ``state`` is one of
    the STATE_* names. Plist existence is never taken as health."""
    run = runner or _default_runner
    plist_path = service_plist_path(home)
    expected_logs = service_log_paths(home, config_path)
    report = {
        "state": STATE_NOT_INSTALLED,
        "label": SERVICE_LABEL,
        "target": service_target(),
        "installed": False,
        "plist_path": plist_path,
        "program": None,
        "code_path": None,
        "arguments": None,
        "log_paths": list(expected_logs),
        "pid": None,
        "last_exit_status": None,
        "last_exit_code": None,
        "runtime_lock_held": None,
        "runtime_lock_path": os.path.join(
            service_state_dir(home, config_path), RUNTIME_LOCK_FILE_NAME
        ),
        "detail": "",
    }
    if not os.path.lexists(plist_path):
        report["detail"] = "no job definition at %s" % plist_path
        return report
    report["installed"] = True
    parsed, error = _load_existing_plist(plist_path)
    if error is not None:
        report["state"] = STATE_FOREIGN
        report["detail"] = (
            "job definition at %s does not parse (%s)" % (plist_path, error)
        )
        return report
    if not _owned_definition(parsed):
        report["state"] = STATE_FOREIGN
        # Never assume a dict here, even though _load_existing_plist
        # already routed every non-dict root to the error branch.
        label = parsed.get("Label") if isinstance(parsed, dict) else None
        arguments = (
            parsed.get("ProgramArguments") if isinstance(parsed, dict)
            else None
        )
        report["detail"] = (
            "job definition at %s is not a %s Runtime job this tool"
            " owns (root %s, label %r, arguments %r)" % (
                plist_path, ENTRY_SCRIPT_NAME, type(parsed).__name__,
                label, arguments,
            )
        )
        return report
    arguments = list(parsed["ProgramArguments"])
    report["program"] = arguments[0]
    report["code_path"] = arguments[1]
    report["arguments"] = arguments
    report["log_paths"] = [
        parsed.get("StandardOutPath") or expected_logs[0],
        parsed.get("StandardErrorPath") or expected_logs[1],
    ]
    # The lock lives beside the config the JOB runs, which is the
    # --config it was installed with (or the default state dir).
    bound_config = arguments[3] if len(arguments) == 5 else None
    lock_dir = service_state_dir(
        home, config_path if config_path is not None else bound_config
    )
    report["runtime_lock_path"] = os.path.join(
        lock_dir, RUNTIME_LOCK_FILE_NAME
    )
    lock_held, lock_detail = runtime_status(lock_dir)
    report["runtime_lock_held"] = bool(lock_held)
    bootstrapped, job, detail = _observe_job(run)
    if bootstrapped is None:
        report["state"] = STATE_UNOBSERVABLE
        report["detail"] = detail
        return report
    if bootstrapped:
        pid = job.get("PID")
        raw_exit = job.get("LastExitStatus")
        report["pid"] = pid if isinstance(pid, int) else None
        report["last_exit_status"] = (
            raw_exit if isinstance(raw_exit, int) else None
        )
        report["last_exit_code"] = _decode_exit_status(raw_exit)
    if report["pid"] is not None:
        if lock_held:
            report["state"] = STATE_READY
            report["detail"] = (
                "pid %d is running and holds the runtime lock; the"
                " Runtime is claiming authorized missions"
                % report["pid"]
            )
        else:
            report["state"] = STATE_RUNNING
            report["detail"] = (
                "pid %d is running but the runtime lock is not held"
                " yet (starting up, or not past config load)"
                % report["pid"]
            )
        return report
    if lock_held:
        report["state"] = STATE_DEGRADED
        report["detail"] = (
            "the runtime lock is held but the supervised job has no"
            " PID: a Runtime OUTSIDE this service (for example a manual"
            " `dirun run`) is active. Only that one survives; the"
            " supervised job takes over when it exits. %s" % lock_detail
        )
        return report
    if not bootstrapped:
        report["state"] = STATE_INSTALLED_STOPPED
        report["detail"] = "%s; start it with `start`. %s" % (
            detail, STOPPED_TRANSIENCE_NOTE,
        )
        return report
    code = report["last_exit_code"]
    if code in (None, 0):
        report["state"] = STATE_INSTALLED_STOPPED
        report["detail"] = (
            "bootstrapped but no instance is running (last exit"
            " status %r); start it with `start`. %s" % (
                report["last_exit_status"], STOPPED_TRANSIENCE_NOTE,
            )
        )
        return report
    report["state"] = STATE_DEGRADED
    raw = report["last_exit_status"]
    if code == RUNTIME_LOCKED_EXIT_CODE and raw >= 256:
        report["detail"] = (
            "the last instance exited %d: lock contention. Another"
            " Runtime held %s (a manual `dirun run` most likely);"
            " launchd relaunches every %d s and the supervised job"
            " takes over when that holder exits" % (
                code, report["runtime_lock_path"], THROTTLE_SECONDS,
            )
        )
    elif code == RUNTIME_LOCKED_EXIT_CODE:
        # A raw status below 256 is ambiguous: launchd normally reports
        # a wait status (exit 3 = 768), so a bare 3 may equally be a
        # signal-3 (SIGQUIT) termination. Name both readings.
        report["detail"] = (
            "the last instance ended with raw status %d, which is"
            " either the Runtime's lock-contention exit code (%d:"
            " another Runtime held %s, a manual `dirun run` most"
            " likely) or a signal-3 (SIGQUIT) termination; launchd"
            " relaunches every %d s. Inspect %s to tell them apart" % (
                raw, RUNTIME_LOCKED_EXIT_CODE,
                report["runtime_lock_path"], THROTTLE_SECONDS,
                report["log_paths"][1],
            )
        )
    else:
        report["detail"] = (
            "the last instance exited %d (raw status %r); launchd"
            " relaunches every %d s. Inspect %s" % (
                code, report["last_exit_status"], THROTTLE_SECONDS,
                report["log_paths"][1],
            )
        )
    return report


def doctor(home=None, runner=None, which=None, config_path=None):
    """Status plus host break-glass readiness plus explicit limits."""
    report = service_status(home, runner=runner, config_path=config_path)
    report["tailscale"] = host_readiness.tailscale_readiness(
        runner=runner, which=which,
    )
    report["ssh"] = host_readiness.ssh_readiness(runner=runner)
    report["limitations"] = [GUI_LOGIN_LIMITATION, TAILNET_POLICY_LIMITATION]
    return report


def render_status_text(report):
    lines = [
        "runtime-service: state=%s" % report["state"],
        "  label: %s" % report["label"],
        "  target: %s" % report["target"],
        "  installed: %s (%s)" % (
            "yes" if report["installed"] else "no", report["plist_path"],
        ),
        "  program: %s" % (report["program"] or "-"),
        "  code path: %s" % (report["code_path"] or "-"),
        "  arguments: %s" % (
            " ".join(report["arguments"]) if report["arguments"] else "-"
        ),
        "  pid: %s" % (report["pid"] if report["pid"] is not None else "-"),
        "  last exit status: %s" % (
            report["last_exit_status"]
            if report["last_exit_status"] is not None else "-"
        ),
        "  runtime lock: %s (%s)" % (
            {True: "held", False: "not held", None: "not probed"}[
                report["runtime_lock_held"]
            ],
            report["runtime_lock_path"],
        ),
        "  stdout log: %s" % report["log_paths"][0],
        "  stderr log: %s" % report["log_paths"][1],
        "  detail: %s" % (report["detail"] or "-"),
    ]
    return "\n".join(lines) + "\n"


def render_doctor_text(report):
    text = render_status_text(report)
    text += host_readiness.render_tailscale_text(report["tailscale"])
    text += host_readiness.render_ssh_text(report["ssh"])
    text += "limitations:\n"
    for statement in report["limitations"]:
        text += "  - %s\n" % statement
    return text
