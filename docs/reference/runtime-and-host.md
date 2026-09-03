# Runtime and host

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

## Runtime service

**[IMPLEMENTED / PROVEN]** The DI-REMOTE-2 Runtime is the `target_runtime/`
package with the `dirun` entry script: a separate local process coupled to
the control chain only through the durable workflow authority store. It
claims authorized workflows and advances the full lifecycle; Telegram, the
Gateway, and Codex never invoke it in-process. Its contract and its proof
boundary are in the
[README](../../README.md#remote-target-repository-routing-di-remote-2-v070).
This page covers running it.

`scripts/install.sh` installs the `dirun` wrapper. Run one claim pass
with `dirun once`, the foreground loop with `dirun run`, or install
the optional per-user LaunchAgent:

```bash
scripts/dirun-agent.sh install            # default protected config
scripts/dirun-agent.sh install --config PATH
scripts/dirun-agent.sh uninstall
```

The agent mirrors the tgop LaunchAgent semantics: absolute paths,
RunAtLoad, KeepAlive with a restart throttle, logs beside the
protected state, the validated `codex` directory first on the job
PATH, fail-closed install when `codex` is not resolvable, and a
single-instance lock (the same lock `/status` probes).

## Host requirements

**[IMPLEMENTED / PROVEN]** The trusted Mac is the execution node. Remote
operation requires it to remain available.

The MVP includes an optional per-user macOS LaunchAgent installed by `tgop install-agent`. It runs the outbound long-polling adapter at login, uses `RunAtLoad` and `KeepAlive` with restart throttling, stores logs and durable state in the protected configuration directory, embeds the validated Codex executable path, and refuses concurrent adapter instances. `tgop uninstall-agent` unloads and removes that exact job.

This is the current always-on process model, not a cloud service: Telegram sends updates to the Bot API, and the adapter on the MacBook polls outbound for them. There is no webhook, public listener, or inbound port. The MacBook remains the trusted execution node and must be awake, online, authenticated, and able to access the configured repository.

The remaining always-on work is operational reliability validation and productization while preserving existing execution semantics.

The Mac should:

- remain powered on
- remain connected to the internet
- retain local repository access
- retain Codex authentication
- retain Git authentication
- retain Herdr runtime access
- start the remote adapter automatically at user login
- expose health/status locally
- recover the adapter process if it exits

This does not require moving Herdr into the cloud.

The Mac remains the engineering node.

## What survives a restart today

**[IMPLEMENTED / PROVEN]** Pinned by the adapter and Runtime suites.

- Authority-bearing adapter state is persisted before any external action,
  and the Telegram update offset advances only after accepted state is
  durably stored.
- After a crash or restart the adapter reports queued-but-undispatched work
  as dropped (re-send it) and dispatched-but-unconfirmed work as AMBIGUOUS.
  It is never replayed automatically.
- The Runtime claims from the durable workflow store, so a Runtime restart
  does not lose an authorized workflow. Failed or ambiguous states fail
  closed durably and surface through `/status` with concrete remedies.
- The optional LaunchAgents for `tgop` and `dirun` use RunAtLoad and
  KeepAlive with a restart throttle, so an exited process is restarted at
  login and after failure. A single-instance lock refuses a second
  concurrent adapter or Runtime.
- A stopped or uninstalled Runtime is an actionable `/status` error naming
  the exact remedy commands, never a silent stall.

## Known limits

**[PLANNED / TARGET]** Host survival is Phase 1 work. Cold reboot, login
ordering, sleep and wake, long sleep, network loss and recovery, DNS
failure, and temporary GitHub, Telegram, or model outages have not been
validated as a matrix; the
[Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md)
tracks them under Iteration 0 and Iteration 8. Break-glass access over
Tailscale and SSH, service identity that exposes running versus on-disk
commit, and a readiness dependency graph are on the same list. A stale
process should never look healthy simply because it is still running;
today that detection is manual. The wiki
[Observation and Recovery](../wiki/Observation-and-Recovery.md) page
describes the target recovery classes and the
[Roadmap](../wiki/Roadmap.md) places them in Phase 1 and Phase 2.
