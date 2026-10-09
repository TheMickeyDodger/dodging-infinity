# On-demand tunnel (`ditunnel.py`)

`ditunnel.py` starts, stops and reports a free Cloudflare **Quick Tunnel**
from a public `https://<random>.trycloudflare.com` URL to the local Grok Bot
MCP endpoint on `127.0.0.1:PORT`. It runs only when you ask for it.

Run it from a local shell you control. That includes Grok Bot's per-command,
user-approved local shell, which you approve every time it runs a command.
No MCP tool reaches it, and Dodging Infinity grants it to no worker.

## Commands

All output is one JSON object. The exit codes are:

- `0` done, or nothing to do;
- `3` refused or failed;
- `1` the foreground tunnel exited by itself;
- `2` usage.

```bash
ditunnel.py --state-dir DIR on --port PORT [--cloudflared /abs/cloudflared] [--stop-grace S]
ditunnel.py --state-dir DIR status
ditunnel.py --state-dir DIR off
ditunnel.py --state-dir DIR forget
```

`DIR` is an absolute, owner-only directory (mode 0700; anything wider is
refused). Its path must be short enough for a Unix socket: at most 92 bytes.

- **`on`** starts the tunnel and prints its CURRENT URL. Repoint the Grok Bot
  connector at that URL; it changes on every start.
  - If a tunnel with the same configuration is already running, `on` reports
    it (`already_on`).
  - If one with a DIFFERENT configuration is running (another port, or
    another `cloudflared`), `on` refuses and names each difference. It never
    presents that tunnel's URL as the one you asked for.
- **`status`** reports what is actually running, observed now. Its states
  are `on`, `off`, `unverified` and `unknown`. A URL appears as `url` (active)
  only while the controller observes its process alive. Otherwise it is
  `unverified_url` (the controller is gone) or `stale_url` (the process is
  gone).
- **`off`** stops the tunnel through its controller and reports `observed`
  only when the whole process group is gone. Repeated, it does nothing.
- **`forget`** moves a record the tool cannot resolve aside, intact, into
  `DIR/retained/`. Use it only after you have inspected the record and dealt
  with any process yourself. It signals nothing, and it refuses while the
  record still names a live tunnel.

## What it runs, and the cost boundary

```text
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:PORT --http-host-header 127.0.0.1:PORT
```

According to Cloudflare's documentation
([Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
and the [Wrangler `tunnel` commands](https://developers.cloudflare.com/workers/wrangler/commands/tunnel/)),
a Quick Tunnel is free and needs no Cloudflare account. That is the
documentary basis for the cost boundary. It is documentation, not evidence
that the tunnel works end to end; nothing here ran the real `cloudflared`.
See [Sources](#sources).

The tool never logs in, never creates a named tunnel and never installs
anything. Without `cloudflared` on PATH (or `--cloudflared`) it refuses.

**Rejected: email-PIN access (`--allowed-mail`).** It requires an interactive
browser flow, which a non-interactive MCP client cannot complete.

**Limits of a Quick Tunnel, from the vendor documentation**
([Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)):

- **The URL is temporary.** It changes on every start, and it stops working
  when its `cloudflared` stops.
- **No uptime guarantee.** Quick Tunnels are for testing.
- **No SSE.** Quick Tunnels do not support Server-Sent Events.
  `grok_bot/server.py` answers with a single SSE event when a client accepts
  only `text/event-stream`, and that path is not usable over a quick tunnel.
  The JSON response path is the usable one, and `server.py` states this. Its
  SSE branch is kept for forwarders that can carry it.
- **The Host header is [U].** `--http-host-header` presents the `Host` that
  the endpoint's DNS-rebinding guard requires. That `cloudflared` honours it
  for a Quick Tunnel is unverified until a live acceptance.

## Who owns the tunnel: the controller

`on` starts a small CONTROLLER process (`tunnel_control.controller`), detached
in its own session, and relays its one-line startup result. The controller
owns the tunnel for its whole life:

1. It writes a `starting` record (`DIR/tunnel.json`) under the state lock.
2. It spawns `cloudflared` through a gate, in `cloudflared`'s own session and
   process group (`setsid`).
   - The gate execs `cloudflared` only after the tunnel's pid and `ps -o
     lstart=` start time have been persisted.
   - If the controller fails or dies first, the gate exits without ever
     running `cloudflared`. So a `cloudflared` the record does not name
     cannot be running.
3. It waits for a URL in that run's own log. It then requires `cloudflared`
   to stay alive through a short settle period, so a URL printed by a
   process that then exits is never reported.
4. It serves `status` and `stop` on `DIR/ctl.sock`.
   - **Reads:** each request read has ONE total deadline (2 s), not just a
     per-receive timeout, so a client dribbling bytes cannot hold it.
   - **Size:** a request is at most 65536 bytes, its newline included.
     Exactly that is accepted, and one byte more is refused. The size is
     checked after every chunk, the one that completes the line included.
   - **Replies:** each reply is bounded as a whole (1 s), and sent in pieces
     of at most 0.05 s each.
   - **Prompt stop:** a pending stop is checked at every one of those waits
     (between receives, once the line is complete, and between reply pieces)
     and abandons the request or reply at once. So a client that dribbles,
     or never reads, delays a stop by at most 0.05 s.
   - **The one exception:** the reply to a client's own `stop` request. The
     controller is already stopping by then and ignores further stop
     signals, so that reply can take up to 1 s, which the arithmetic below
     counts.

**Why ownership is structural.** The controller keeps `cloudflared` as its
own UNREAPED child. Until it reaps that child, the pid and the process-group
id cannot be given to any other process. Signals the controller sends to that
group therefore reach the tunnel's own processes and nothing else, with no
PID-reuse question and no reliance on a timestamp.

**Anchored shutdown,** for `off`, a stop signal, or `cloudflared` exiting by
itself:

1. SIGTERM to the group.
2. A bounded wait (`--stop-grace`, default 10 s) until the leader has exited
   AND no member can still be signalled.
3. Otherwise SIGKILL to the group. It is still anchored: the leader is
   unreaped, alive or a zombie.
4. Reap the leader.
5. Report `observed` only when the group is gone.

A leader that exits while a member ignores SIGTERM is still an unreaped
zombie, so that member is reached by the SIGKILL to the same group.

**If a member survives even SIGKILL, the anchor is held only for a bounded
time.** The controller retries the whole attempt (SIGTERM, grace, SIGKILL)
every second, for up to 300 s, without reaping the leader, so the anchor
holds while it retries. If a member is still alive when that budget runs out:

- the record is kept as `stop_unconfirmed`;
- the controller exits, and the anchor is RELEASED.

That is an ordinary way to lose the anchor, with no SIGKILL or crash of the
controller. Recovery is then the separately authorized manual case below; the
tool does nothing on inference.

**The record is removed only after an observed stop.** Every way the
controller can end does one of three things: it runs this shutdown to an
observed stop, it keeps the anchor deliberately, or it releases the anchor
with the record kept.

- **A failure or stop signal during startup:** the shutdown runs, with the
  same bounded retries, before the error is reported. If the stop is still
  not observed when the retries run out, the record is kept as
  `stop_unconfirmed` and the anchor is released.
- **An `on` client that vanished before reading the result:** the anchor is
  KEPT, and the controller goes on serving.
- **An unexpected error while serving:** the shutdown runs, then the error
  propagates.
- **Retry exhaustion while stopping:** a member is still alive after the
  300 s retry budget. The record is kept as `stop_unconfirmed` and the anchor
  is RELEASED, so recovery is manual.

**Not covered.** A member that moved itself into another process group or
session is outside the group, and is not reached. `cloudflared` is not known
to do that.

## When the controller is gone: report, never signal

The controller can be gone in three ways, and in each its anchor is gone with
it:

- it was SIGKILLed or crashed hard;
- launchd terminated the job before its stop completed (see "The optional
  launchd job");
- it exhausted its retry budget with a member still alive (above). That is an
  ordinary path, not a crash.

The record still names the tunnel's pid, process group and start time. Those
support only an INFERENCE that the tunnel is alive: the recorded pid holds a
process whose `ps -o lstart=` value is EQUAL to the recorded one, and which
leads its own group.

That equality is identity unchanged AS OBSERVED. `lstart` has one-second
granularity, so equality is not proof that the pid was never reused. A check
and a later signal are separate operations: acting on it is NOT atomic
identity-safe signalling. Even a "reversible" SIGSTOP would freeze
whatever process holds the pid, and SIGCONT is no undo: it can resume a
process that something else stopped on purpose.

So without the controller, the tool sends NO signal of any kind:

- `status` reports `unverified`, with the URL only as `unverified_url`.
- `off` refuses, keeps the record, and names the recorded pid, process group
  and start time.

Hard-kill recovery of a tunnel whose controller is gone is NOT SUPPORTED by
this tool. You do it manually, under your own authority.

### Manual recovery

1. Read the recorded identity: `ditunnel.py --state-dir DIR status` (or `DIR/tunnel.json`).
2. Inspect the group yourself: `ps -axo pid,pgid,lstart,command | awk '$2 == PGID'`.
   Confirm the processes are the tunnel and its children.
3. Stop the group yourself, for example `kill -TERM -PGID`, then if needed
   `kill -KILL -PGID`.
4. Run `ditunnel.py --state-dir DIR off`. Once the record names nothing
   running, `off` clears it. Alternatively, `forget` moves the record aside.

The same applies when the controller is gone AND the leader has already
exited while members of its group remain. `status` reports `unknown`, and
`off` refuses and keeps the record.

`target_runtime.process_ownership.reap_group` is not used by this tool. When
group verification fails, its fallback SIGKILLs the single pid, which after a
non-atomic identity check could reach a process the pid was reused for.

## What this establishes, and what it does not

All signalling goes through the anchor. `off` signals only through a live
controller, and the controller signals only the process group of its own
unreaped child. Without a controller nothing is signalled. So this tool never
signals a process it did not start: never an unrelated `cloudflared`, and
never a pid now held by an unrelated process. Nothing enumerates processes or
matches a name.

The ownership rule is a workflow guardrail, not designed to contain processes
running with the user's own privileges: it does not stop such a process, a
Herdr worker included, from controlling the tunnel. The record and the socket
are the tool's bookkeeping, not access control.

**Local-shell only.** No MCP tool exposes tunnel lifecycle, and no worker role
or operator contract is granted it. `tests/test_tunnel_control.py` pins both
structurally. That pin is necessary, but it is not sufficient: it removes the
designed routes, not every route.

**Grok Bot is not distinguished.** The tool cannot tell Grok Bot's approved
local shell from any other process running as the same user, because they
have the same identity to the operating system. Grok Bot's per-command
approval protects what Grok Bot runs. It does not stop another local process
from running this tool, and no separate worker identity is deployed, so that
limitation stands.

Per the Grok Bot documentation
([computer and apps](https://docs.x.ai/grok-bot/computer-and-apps),
[approvals, security and privacy](https://docs.x.ai/grok-bot/approvals-security-and-privacy)),
local execution and cloud execution are separate capabilities, and approval is
asked every time by default. That is vendor documentation, not proof that this
account or device has local execution enabled. See [Sources](#sources).

## A session, step by step (for a later, separately approved step)

These are instructions for a later step that needs its own human approval.
No step was performed in this Mission, and fixture tests prove mechanism shape
only.

1. **Start.** Run `ditunnel.py --state-dir DIR on --port PORT` through Grok
   Bot's per-command, user-approved local shell. Its default policy is Ask
   every time, so you approve that exact command. It prints the CURRENT
   `trycloudflare.com` URL.
2. **Repoint the connector** to that URL plus `/mcp`. The URL changes on every
   start, so this is per session, not one-time.
3. **Work.** Use the Grok Bot tools over the AUTHENTICATED HTTP MCP endpoint
   (bearer token). Any approval needs its local arming first
   ([grok-bot.md](grok-bot.md#local-arming-the-approval-boundary)).
4. **Stop.** Run `ditunnel.py --state-dir DIR off` at session end, then
   `status` to confirm. `status` reports the observed state and never an old
   URL as active.

## Migrating from the reported permanent tunnel (a plan only)

The Mission's handoff reports a permanently running tunnel: launchd label
`com.dodginginfinity.grokbot.task8.tunnel`, forwarding to `127.0.0.1:63763`.

- **Reported, not verified.** The launchd LABEL and the forwarding target
  are REPORTED BY THE REQUEST.
- **The job was not inspected.** Its JOB DEFINITION was never read, so its
  OWNERSHIP IS UNVERIFIED.
- **Ownership is not shown by appearances.** A process whose argv or port
  matches that description would NOT prove it is that launchd job.
- **A matching process was observed once.** It was observed read-only, in
  one host-wide `ps` read: no configuration or secret was read, and no signal
  was sent. That observation established NOTHING about launchd ownership,
  for the reason just given.
- **No job-management action was taken.** Migration means unloading that job
  and adopting this default-OFF artifact plus per-session `on` / `off`, and it
  requires a SEPARATE human approval. No job-management, installation or
  migration action was performed: the job was not modified, started, stopped,
  loaded or unloaded, and migration was not performed.
- **Earlier stray signals are a separate unknown.** Whether any earlier test
  or smoke-script signal reached an unrelated process is UNKNOWN, as
  disclosed in the review evidence for this change. This document claims
  neither that the job was affected nor that it was unaffected.

**What the operator verifies at that later step, not now:**

1. That the label exists in the operator's own launchd domain, and what its
   plist actually runs (program, arguments, port), from the job definition,
   not from a process that merely looks similar.
2. That stopping it is acceptable: nothing else depends on its URL, and the
   connector will be repointed.
3. That after unloading it (the operator's own `launchctl` action), its
   tunnel is gone. Then: `ditunnel.py on` with the chosen port, repoint the
   connector, and confirm with `status`.
4. Whether to install this artifact at all. Per-session `on` / `off` from the
   local shell needs no launchd job.

## Sources

Vendor documentation only, each with the claim it supports and its date.
None is evidence that this code works end to end.

- **<https://docs.x.ai/grok-bot/computer-and-apps>** (updated 2026-10-08):
  cloud execution and local-computer execution are separate capabilities.
  Local execution is subject to enable-and-approve.
- **<https://docs.x.ai/grok-bot/approvals-security-and-privacy>** (updated
  2026-10-06):
  - Execution on Local Computer offers Ask every time, Always allow and
    Never allow, and the default is Ask every time;
  - settings are per computer, and the prompt offers Allow once;
  - the controls are the same on iPhone.
- **<https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/>**
  (updated 2026-09-30):
  - `cloudflared tunnel --url` starts one;
  - the `trycloudflare.com` URL is temporary, changes on every start, and
    ceases when its `cloudflared` stops;
  - no Server-Sent Events, and no uptime guarantee.
- **<https://developers.cloudflare.com/workers/wrangler/commands/tunnel/>**
  (updated 2026-04-23): Quick Tunnels are free and need no account. This is
  the documentary basis for the cost boundary.
  - **Its date is older.** It was last updated in April 2026, while the other
    three pages are from September and October 2026. That is adequate for a
    free-and-accountless statement, but the four are not equally current.

**Where these come from.** The two `docs.x.ai` entries are from read-only
public documentation, checked 2026-10-08. The two Cloudflare entries and
their dates (the Quick Tunnels page 2026-09-30, the Wrangler page
2026-04-23, read from that page's header) were recorded from the vendors'
pages during the review of this change.

**What the dates are.** They are documentary metadata only: when each vendor
last updated its page. They establish nothing about whether this code works,
about live tunnel behaviour, or about this account or device (the
fixture-only boundary in [grok-bot.md](grok-bot.md#what-the-tests-prove-and-what-they-do-not)). None was re-fetched for this
document.

## The optional launchd job

`scripts/ditunnel/com.dodginginfinity.ditunnel.plist` is an ARTIFACT ONLY.
Nothing installs, loads, bootstraps or kickstarts it.

- **Default off.** `RunAtLoad` is false and there is no `KeepAlive`, so
  loading it starts nothing and launchd never restarts it.
- **It runs only on demand.** It runs when you kickstart it from a local
  shell, and stops on `launchctl kill TERM` or `bootout`, or on `ditunnel.py
  off`.
- **The job IS the controller.** The job's main process is
  `ditunnel.py foreground`, which holds `cloudflared` as its own unreaped
  child, exactly as the detached controller does.
- **`ExitTimeOut` is 420 s,** longer than the controller's worst-case bounded
  stop at the default `--stop-grace`, which is at most 343.05 s from launchd's
  SIGTERM:

  | Step | Seconds |
  |---|---|
  | Observing the stop: 0.05 s at whichever wait the controller is in (accept, receive, or reply piece), plus the local work between waits (at most one status computation: a read of at most 1 MiB of its own log, one regex, one JSON encoding). The local work is ALLOWED 1 s as a stated assumption; it is not measured | 1.05 |
  | The first attempt: SIGTERM grace 10, then two 5 s waits | 20 |
  | The reply to a client's `stop` request, if there is one | 1 |
  | The retry window, which includes the second attempt | 300 |
  | A final 1 s pause and attempt, begun just before the window closes | 21 |

  With these values, launchd's own SIGKILL does not cut the bounded stop of
  the tunnel's processes short.
- **Record bookkeeping is not bounded, and what that means depends on how the
  stop ended.** After the stop, removing or marking the record takes the
  state lock, which can wait indefinitely on another process holding it.
  - **The stop was observed:** the group was observed gone, so nothing is
    running. If launchd ends the job during the wait, the record is left
    naming nothing that runs, and a later `off` clears it.
  - **The retries ran out (`observed` false):** members may still be alive.
    The controller takes the same unbounded lock to save the record as
    `stop_unconfirmed`. If launchd ends the job during that wait, or before
    the save, the survivors are left running, the anchor is lost, and the
    record may still say `running`, which is stale. `status` then reports by
    inference only (`unverified` or `unknown`), `off` refuses, and recovery
    is manual. This is the retry-exhaustion anchor-loss path above, with the
    record possibly not yet marked.
- **The plist passes no `--stop-grace`.** A larger grace raises every
  attempt, and needs a larger `ExitTimeOut`.
- **If launchd terminates the job before the stop completes anyway,** that is
  an anchor loss. That could happen with a shorter `ExitTimeOut`, a larger
  grace, or launchd behaving otherwise ([U] until a live install). The record
  is left as last saved: `running` while a requested stop is in progress,
  `stop_unconfirmed` after a startup cleanup. Recovery is manual.

**Installing it is a separate, human step:**

1. Replace every placeholder.
2. Copy the file to `~/Library/LaunchAgents/`.
3. `launchctl bootstrap gui/$(id -u) <plist>`.
4. When wanted, `launchctl kickstart gui/$(id -u)/com.dodginginfinity.ditunnel`.

None of that is done or tested here.

### Amendment 8's questions, answered from this mechanism

**1. launchd descendant cleanup and lifetime.**

- **Mechanism.** The job is long-running, not a one-shot `on` that
  backgrounds a process and exits. The job's main process holds the tunnel
  and stops it anchored on SIGTERM, before it exits.
- **Evidence (fixtures, `ForegroundJobTests`):**
  - SIGTERM to the job stops the tunnel and the member that ignores SIGTERM,
    and the job exits 0;
  - `off` from a shell stops the job;
  - a SIGKILLed job leaves `cloudflared` running in its own session, outside
    the job's group, and the tool then signals nothing for it.
- **Inference.** launchd's documented default (`AbandonProcessGroup` false)
  kills processes remaining in the JOB's process group when the job exits.
  The tunnel is not in that group, so that cleanup would not reach it.
- **[U]** What launchd itself does with this job, and whether the tunnel
  survives a live job's death, is settled only by a live install.

**2. Session behaviour.**

- The tunnel is started with `start_new_session=True`, which performs
  `setsid`. Its pid is its process group and its session, and that is what
  the record stores as `pgid`.
- The detached controller is also started with `setsid` (shell path).
- Reparenting does not change a process group id.
- **Evidence:** `LifecycleTests.test_the_tunnel_leads_its_own_session_and_group`.

**3. Process-start identity.**

- **Normal path:** no signal depends on the start time. The anchor is the
  unreaped child.
- **Classification and reporting:** the recorded start time is compared for
  EQUALITY against the live `ps -o lstart=` value (`common.identity`). An
  unreadable start time at spawn is a startup failure.
- **Limit:** equality is identity unchanged as observed, not proof against
  reuse, and it is never used to signal.
- **Evidence:**
  - `OwnershipTests.test_start_time_equality_is_the_test`;
  - `StartupTests.test_an_unreadable_start_time_is_a_startup_failure`.

**4. `off` after the caller exits.**

- `off` from any later shell reaches the controller over `ctl.sock`. The
  controller signals only its own unreaped child's process group: SIGTERM,
  then, if needed, SIGKILL.
- If the controller is gone, `off` signals nothing and refers to manual
  recovery.
- **Evidence:**
  - `LifecycleTests.test_off_works_after_the_shell_that_ran_on_has_exited`;
  - `OwnershipTests.test_without_its_controller_off_signals_nothing_and_keeps_the_record`.

## Not verified here [U]

- That the real `cloudflared` prints its URL in the form the tool reads.
  Fixtures print the same shape, which is an assumption about the vendor's
  output.
- `cloudflared`'s own SIGTERM handling and grace period.
- That `--http-host-header` works for a Quick Tunnel.
- Any launchd behaviour.
- Whether this account and device can run Grok Bot's local shell.
