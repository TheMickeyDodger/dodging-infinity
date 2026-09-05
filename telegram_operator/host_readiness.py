"""Local break-glass readiness diagnostics: Tailscale and system SSH.

Pure OBSERVATION. Every probe goes through an injected
``runner(argv) -> (returncode, stdout, stderr)`` and an injected
``which``; the complete set of argv this module can ever issue is the
``READ_ONLY_ARGV_ALLOWLIST`` constant, which contains no mutating verb.
Nothing here joins a tailnet, changes ACLs or grants, touches the
firewall, ``authorized_keys``, or any Tailscale or SSH configuration.

Every report carries an explicit, unconditional statement that
tailnet ACLs / grants and other external policy are manual and NOT
verified locally, so a ``connected`` reading is never mistaken for
"break-glass access works".
"""

import json
import shutil
import subprocess

TAILSCALE_BINARY_NAME = "tailscale"
TAILSCALE_STATUS_ARGV = ("tailscale", "status", "--json")
SYSTEMSETUP_REMOTE_LOGIN_ARGV = ("systemsetup", "-getremotelogin")
LAUNCHCTL_PRINT_DISABLED_ARGV = ("launchctl", "print-disabled", "system")
# The COMPLETE set of commands this module can issue.
READ_ONLY_ARGV_ALLOWLIST = (
    TAILSCALE_STATUS_ARGV,
    SYSTEMSETUP_REMOTE_LOGIN_ARGV,
    LAUNCHCTL_PRINT_DISABLED_ARGV,
)
SSHD_LABEL = "com.openssh.sshd"

TAILSCALE_MISSING = "missing"
TAILSCALE_DISCONNECTED = "disconnected"
TAILSCALE_CONNECTED = "connected"
TAILSCALE_UNOBSERVABLE = "unobservable"
TAILSCALE_SSH_ENABLED = "enabled"
TAILSCALE_SSH_UNOBSERVABLE = "unobservable"

SSH_ON = "on"
SSH_OFF = "off"
SSH_UNOBSERVABLE = "unobservable"

TAILNET_POLICY_STATEMENT = (
    "Tailnet ACLs / grants and other external policy (tailnet SSH"
    " rules, device approval, key expiry, network firewalls) are"
    " MANUAL and are NOT verified locally; a connected Tailscale and"
    " an enabled SSH do not by themselves prove that break-glass"
    " access works."
)

# Output is bounded before it is quoted in a detail string.
_DETAIL_LIMIT = 500


class DisallowedArgvError(ValueError):
    """An argv outside READ_ONLY_ARGV_ALLOWLIST was requested."""


def _default_runner(argv):
    completed = subprocess.run(list(argv), capture_output=True, text=True)
    return completed.returncode, completed.stdout, completed.stderr


def _bounded(text):
    return (text or "").strip()[:_DETAIL_LIMIT]


def _run_allowlisted(run, argv):
    """Issue one allowlisted argv; ``(returncode, stdout, stderr)`` or
    ``(None, "", reason)`` when the runner itself failed."""
    # A real exception, not an assert: `python -O` must not be able
    # to strip the allowlist gate.
    if tuple(argv) not in READ_ONLY_ARGV_ALLOWLIST:
        raise DisallowedArgvError(
            "argv %r is not in the read-only allowlist" % (list(argv),)
        )
    try:
        returncode, stdout, stderr = run(list(argv))
    except Exception as exc:
        return None, "", "%s: %s" % (type(exc).__name__, exc)
    return returncode, stdout or "", stderr or ""


def tailscale_readiness(runner=None, which=None):
    """Observe Tailscale: ``missing`` / ``disconnected`` /
    ``connected`` / ``unobservable``. Tailscale-SSH capability is read
    from ``Self.sshHostKeys`` only; when absent it is reported
    ``unobservable``, never inferred."""
    run = runner or _default_runner
    resolve = which or shutil.which
    report = {
        "state": TAILSCALE_UNOBSERVABLE,
        "binary": None,
        "backend_state": None,
        "online": None,
        "tailscale_ssh": TAILSCALE_SSH_UNOBSERVABLE,
        "detail": "",
        "policy": TAILNET_POLICY_STATEMENT,
    }
    binary = resolve(TAILSCALE_BINARY_NAME)
    if not binary:
        report["state"] = TAILSCALE_MISSING
        report["detail"] = (
            "the %r binary is not resolvable on PATH (the macOS app"
            " bundle keeps it inside Tailscale.app; put it on PATH to"
            " observe it)" % TAILSCALE_BINARY_NAME
        )
        return report
    report["binary"] = binary
    returncode, stdout, stderr = _run_allowlisted(run, TAILSCALE_STATUS_ARGV)
    if returncode is None:
        report["detail"] = "tailscale status could not be run (%s)" % stderr
        return report
    document = None
    if stdout.strip():
        try:
            document = json.loads(stdout)
        except ValueError:
            document = None
    if not isinstance(document, dict):
        if returncode != 0:
            # No JSON and a failure: the daemon is unreachable or the
            # client is not connected to it.
            report["state"] = TAILSCALE_DISCONNECTED
            report["detail"] = (
                "tailscale status exited %d without a status document"
                " (daemon unreachable?): %s" % (
                    returncode, _bounded(stderr) or _bounded(stdout),
                )
            )
            return report
        report["detail"] = "tailscale status output did not parse as JSON"
        return report
    backend = document.get("BackendState")
    report["backend_state"] = backend if isinstance(backend, str) else None
    self_node = document.get("Self")
    if isinstance(self_node, dict):
        online = self_node.get("Online")
        report["online"] = online if isinstance(online, bool) else None
        host_keys = self_node.get("sshHostKeys")
        if isinstance(host_keys, list) and host_keys:
            report["tailscale_ssh"] = TAILSCALE_SSH_ENABLED
    if report["backend_state"] is None:
        report["detail"] = "status document carries no BackendState"
        return report
    if backend == "Running" and report["online"] is True:
        report["state"] = TAILSCALE_CONNECTED
        report["detail"] = "BackendState Running, Self.Online true"
    elif backend in ("Stopped", "NeedsLogin", "NoState") or (
        backend == "Running" and report["online"] is False
    ):
        report["state"] = TAILSCALE_DISCONNECTED
        report["detail"] = "BackendState %s, Self.Online %r" % (
            backend, report["online"],
        )
    elif backend == "Running":
        report["detail"] = (
            "BackendState Running but Self.Online is not observable"
        )
    else:
        report["detail"] = "BackendState %r is not a known state" % backend
    return report


def _parse_remote_login(stdout, stderr):
    """``("on"|"off"|None, reason)`` from ``systemsetup`` output."""
    text = (stdout or "") + "\n" + (stderr or "")
    lowered = text.lower()
    if "remote login: on" in lowered:
        return SSH_ON, "systemsetup reports Remote Login: On"
    if "remote login: off" in lowered:
        return SSH_OFF, "systemsetup reports Remote Login: Off"
    reason = _bounded(text) or "no output"
    if "administrator" in lowered or "root" in lowered or (
        "privilege" in lowered
    ):
        return None, (
            "systemsetup requires administrator privileges (%s)" % reason
        )
    return None, "systemsetup output not understood (%s)" % reason


def _parse_print_disabled(stdout):
    """``("on"|"off"|None, reason)`` from ``launchctl print-disabled``:
    the sshd label's disabled flag, when it is listed at all."""
    for raw_line in (stdout or "").splitlines():
        line = raw_line.strip()
        if SSHD_LABEL not in line or "=>" not in line:
            continue
        value = line.split("=>", 1)[1].strip().rstrip(";").strip().lower()
        if value in ("enabled", "false"):
            return SSH_ON, "launchctl print-disabled lists %s enabled" % (
                SSHD_LABEL,
            )
        if value in ("disabled", "true"):
            return SSH_OFF, "launchctl print-disabled lists %s disabled" % (
                SSHD_LABEL,
            )
        return None, "launchctl print-disabled value %r not understood" % (
            value,
        )
    return None, "launchctl print-disabled does not list %s" % SSHD_LABEL


def ssh_readiness(runner=None):
    """Observe system SSH (Remote Login): ``on`` / ``off`` /
    ``unobservable``. A privilege failure of the primary probe is
    ``unobservable`` WITH the reason, never ``off``."""
    run = runner or _default_runner
    probes = []
    state = None
    detail = ""
    returncode, stdout, stderr = _run_allowlisted(
        run, SYSTEMSETUP_REMOTE_LOGIN_ARGV
    )
    if returncode is None:
        primary, reason = None, "systemsetup could not be run (%s)" % stderr
    else:
        primary, reason = _parse_remote_login(stdout, stderr)
        if primary is None and returncode != 0:
            reason = "%s; exit %d" % (reason, returncode)
    probes.append({
        "argv": list(SYSTEMSETUP_REMOTE_LOGIN_ARGV),
        "result": primary or SSH_UNOBSERVABLE,
        "reason": reason,
    })
    if primary is not None:
        state, detail = primary, reason
    else:
        returncode, stdout, stderr = _run_allowlisted(
            run, LAUNCHCTL_PRINT_DISABLED_ARGV
        )
        if returncode is None:
            secondary, secondary_reason = None, (
                "launchctl print-disabled could not be run (%s)" % stderr
            )
        elif returncode != 0:
            secondary, secondary_reason = None, (
                "launchctl print-disabled exited %d: %s" % (
                    returncode, _bounded(stderr) or _bounded(stdout),
                )
            )
        else:
            secondary, secondary_reason = _parse_print_disabled(stdout)
        probes.append({
            "argv": list(LAUNCHCTL_PRINT_DISABLED_ARGV),
            "result": secondary or SSH_UNOBSERVABLE,
            "reason": secondary_reason,
        })
        if secondary is not None:
            state, detail = secondary, "%s (primary probe: %s)" % (
                secondary_reason, reason,
            )
        else:
            state, detail = SSH_UNOBSERVABLE, "%s; %s" % (
                reason, secondary_reason,
            )
    return {
        "state": state,
        "probes": probes,
        "detail": detail,
        "policy": TAILNET_POLICY_STATEMENT,
    }


def render_tailscale_text(report):
    return (
        "tailscale: state=%s\n"
        "  binary: %s\n"
        "  backend state: %s\n"
        "  online: %s\n"
        "  tailscale ssh: %s\n"
        "  detail: %s\n"
    ) % (
        report["state"], report["binary"] or "-",
        report["backend_state"] or "-",
        "-" if report["online"] is None else str(report["online"]).lower(),
        report["tailscale_ssh"], report["detail"] or "-",
    )


def render_ssh_text(report):
    lines = ["ssh: state=%s" % report["state"]]
    for probe in report["probes"]:
        lines.append("  probe %s: %s (%s)" % (
            " ".join(probe["argv"]), probe["result"], probe["reason"],
        ))
    lines.append("  detail: %s" % (report["detail"] or "-"))
    return "\n".join(lines) + "\n"
