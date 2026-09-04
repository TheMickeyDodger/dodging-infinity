# Operations

The operator reference for Dodging Infinity: install, repository setup, running
Missions, observation, Herdr, remote control, the host, and the Git approval
gates. The [README](../README.md) shows the happy path; this page is the
detail behind it.

Everything on this page describes what ships in this checkout. Where a
paragraph names a planned migration or a known limit, it says so in words. The
system these commands operate is described in
[architecture.md](architecture.md); what comes next is on the
[roadmap](roadmap.md).

If a command here disagrees with `herdctl --help` in your checkout, the help
output wins and this page is stale.

## 1. Requirements

Dodging Infinity runs locally. The remote workflow is built around one trusted
machine that stays available while Missions run.

| Requirement | Detail |
|---|---|
| Python 3.9 or newer | The runtime and CLI. Standard library only: there is no `pip install` step for this project. |
| Git | Repository state and delivery. |
| macOS | Required for the full remote workflow, which uses per-user LaunchAgents. Linux is covered for development and CI. |
| [Herdr](https://github.com/herdrdev/herdr) on `PATH` | The engineering capability. Required for engineering Missions. |
| Claude Code | Herdr roles on Claude-backed presets. |
| Codex CLI | The current reference Operator, and the Reviewer role in the `max-quality` preset. |
| GitHub credentials | Repository access and delivery targets. |
| A Telegram bot token | Remote Mission control. Optional. |
| Tailscale / SSH | Break-glass access to the trusted host. Optional. |

CI runs every suite on macOS and Ubuntu against Python 3.9 and 3.13.

The trusted Mac is the execution node. It owns the repositories, Git and Codex
credentials, the Gateway, the Herdr runtime and agents, local test
environments, repository-scoped `.herd` state, and the commit and push gates.
Section 10 lists what it must do and what survives a restart.

## 2. Install

```bash
git clone https://github.com/TheMickeyDodger/dodging-infinity.git
cd dodging-infinity
bash scripts/install.sh
```

The installer writes four wrappers into `~/.local/bin`:

```text
herdctl
codexgw
tgop
dirun
```

If that directory is not on your path:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Install the machine-local Git safety guard once per Mac, after installing or
upgrading Herdr:

```bash
herdctl safety-install
```

This maintains the Claude runtime command guard while preserving existing user
Auto Mode configuration. Per-repository Git commit, history, and push guards
are installed by `herdctl init` and `herdctl upgrade`, not by this command.

## 3. Repository setup

Initialize a repository:

```bash
herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'npm test && npm run build'
```

If the verification command is not known yet:

```bash
herdctl init --alias my-repo --preset max-quality
herdctl set-test 'npm test && npm run build' --repo my-repo
```

The repository receives isolated Herdr runtime configuration and Git authorization boundaries.


### Upgrade an existing repository

```bash
cd ~/code/internal

herdctl upgrade --repo example-repo
herdctl doctor --repo example-repo
```

Existing Herdr safety boundaries, repository isolation, review validation, and Git authorization controls remain intact.

## 4. Presets

List presets:

```bash
herdctl presets
```

Current built-ins:

```text
all-claude       Claude-only subscription herd using Fable/Opus with auto mode
conservative     Claude-only herd retaining explicit edit approvals
max-quality      Claude Fable supervisor/executor + Opus lead + GPT-5.6 Sol high read-only reviewer
```

Apply one:

```bash
herdctl preset max-quality --repo example-repo
```

Presets assign runtimes/models/permissions.

They do not alter the orchestration hierarchy.

## 5. Start and bootstrap

Check the machine and the repository before dispatching work:

```bash
herdctl doctor --repo my-repo
herdctl health --repo my-repo
```

`doctor` answers whether the environment, binaries, runtime kinds, and Git
guards are installed. `health` answers whether this repository's Herdr is
operational and usable right now. Both are read-only.

Start the herd:

```bash
herdctl bootstrap --repo my-repo
```

`--force` re-bootstraps a herd whose agents are already registered.

## 6. Create and run a Mission

The short form dispatches an objective directly:

```bash
herdctl task \
  'Find the failing test, explain what is wrong, fix it, and verify the result. Do not commit.' \
  --repo my-repo
```

The long form creates a durable mission contract first, then dispatches it.
Both are covered in full under Herdr operations below.

## 7. Status, observation, and health

Four commands answer four different questions:

- `doctor`: are the environment, binaries, runtime kinds, and Git guards installed?
- `status`: what is the Herdr currently doing?
- `health`: is this repository's Herdr operational and usable right now?
- `observe`: what does all bounded persisted and live-queryable state say right now?

```bash
herdctl status --repo my-repo
herdctl task-status --repo my-repo
herdctl observe --repo my-repo
herdctl observe --repo my-repo --json
```

### `herdctl health`

```text
herdctl health [--repo NAME]
```

`health` is strictly read-only.

It checks:

- Herdr configuration
- server reachability
- runtime state
- expected/live agents
- task-state readability

Valid workflow states such as:

```text
idle
working
done
blocked
```

are informational rather than infrastructure failures.

Missing, unreachable, malformed, or unknown required infrastructure fails with actionable diagnostics and a nonzero exit.

Healthy state returns:

```text
Health: READY
```

with exit `0`.

Agent probing remains bounded.

### Reading a health report

`health` answers one question: is this repository's Herdr operational and
usable right now? It reads configuration and runtime state, probes the
expected live agents within a fixed bound, and checks that task state is
readable. A workflow state of `blocked` or `idle` is information about the
work, not a failure of the infrastructure, and does not change the exit
code. A missing binary, an unreachable server, or malformed required state
does. The command repairs nothing; every finding is a diagnostic with an
actionable message.

### Relationship to `herdctl observe`

`health` is a readiness probe with a pass or fail exit. `observe` is a
point-in-time projection of everything bounded that can be read about the
herd, with a schema version and a `completeness` field that describes
visibility only. Both are strictly read-only: neither mutates, repairs,
prompts an agent, changes workflow, or controls execution. Observation is a
reporting surface, not a gate. The DI-REMOTE-2 Runtime consumes the same
read-only projection to observe a target herd, under source-scoped
completeness, and the observation bounds it inherits are the ones listed
below.

### What observation does not tell you

Read this before the architecture, not after it. These are limits of the
EVIDENCE, not gaps in the implementation, and each is pinned by a named test —
see "Claim-to-pin map" below for which one.

- **The model a RUNNING agent uses is not observable through the agent
  interface (F1).** `herdctl observe` reports `configured_model` — the model a
  role's CONFIGURATION asks for — and states that limit in its own diagnostics.
  The projection carries no `running_model` field, no `model_observable` flag
  and no verdict about a running model, because such a field would imply a
  distinction the evidence cannot support.
- **A verdict cannot distinguish a model substitution from a restart (F2).**
  Where a substitution preserves the agent's session, the two situations are
  not representably different in what this system can see, and the surface says
  so rather than guessing.
- **A turn record written by a different build of the observer is a claim made
  by different logic.** Skew is reported, naming both the build that wrote the
  record and the build on disk, rather than reconciled silently.

### `herdctl observe` schema v3

The projection is schema version 3. Top-level keys, in the order the projection
emits them:

```text
schema_version
generated_at
completeness
repository
config
vintage
checkpoint
roles
turns
mission
task
runtime
agents
children
reviews
artifacts
recent_tasks
legacy
diagnostics
```

`vintage`, `checkpoint`, `roles` and `turns` arrived after schema v1: they carry
the task a state file belongs to, the artifacts that disagree about it, the
role-to-agent bindings, and the turn records for this task.

Every source section uses a closed source-state vocabulary:

```text
available
missing
malformed
unreadable
unavailable
empty
```

`completeness` describes visibility only.

It does not affect execution.

### What observation says about models

A role's model appears in the projection under the key `configured_model`, and
in the human render as `model-CONFIGURED=`. Both name the CONFIGURATION, and
the unqualified `model` key that preceded them is gone rather than merely
renamed alongside.

The projection also carries the limit itself as a diagnostic, so a consumer
that reads only the JSON meets it without reading this document:

```text
NO running-model value exists in this document and there is no unqualified
`model` key: the model a RUNNING agent uses is not observable through the
agent interface. `configured_model` states intent.
```

A role whose configuration names no model renders `(unset)` — stated as unset
rather than guessed, and not reported as unknown.

### Hard observation bounds

Observation limits are constants rather than repository-controlled values.

Current bounds include:

- 1 MiB state-file limit
- 64 live agent probes
- 32 listed agents
- 10 recent tasks
- 40 review files
- 32 listed children
- 16 artifacts
- 200-character projected strings
- 2000-entry directory scan budget
- 2000-line dirty-file count cap

Bounds are disclosed rather than silently presenting partial information as complete.

### Observation non-goals

`observe` is not:

- a stream
- a daemon
- a control surface
- a repair command
- a TUI
- a replacement Mission Control

It is an instrument panel.

## 8. Herdr operations

Herdr is the engineering organization inside an engineering Mission:
Supervisor, Lead, Executor, Reviewer. What it is, and what a Reviewer APPROVE
does and does not mean, is in
[architecture.md](architecture.md#8-engineering-herdr). This section is the
operating surface.

### Rules and constraints

Repository rules persist:

```bash
herdctl rules --repo example-repo

herdctl rules add \
  "Never modify migrations" \
  --repo example-repo

herdctl rules remove \
  "Never modify migrations" \
  --repo example-repo
```

Task-local rules:

```bash
herdctl task "Implement X" \
  --repo example-repo \
  --rule "Only modify README.md" \
  --rule "Do not commit"
```

Mission rules remain scoped to the dispatched mission.

### Mission contract

Mission state is persisted under:

```text
.herd/state/mission.json
```

Conceptually:

```json
{
  "version": 1,
  "objective": "Fix authentication bug",
  "constraints": [
    "Do not change database schema"
  ],
  "rules": [
    "Preserve backward compatibility"
  ],
  "acceptance_criteria": [
    "Authentication tests pass"
  ],
  "verification": [
    "python3 -m unittest discover -s tests"
  ]
}
```

Create:

```bash
herdctl mission create \
  "Fix authentication bug" \
  --constraint "Do not change database schema" \
  --rule "Preserve backward compatibility" \
  --acceptance "Authentication tests pass" \
  --verification "python3 -m unittest discover -s tests"
```

Inspect:

```bash
herdctl mission show
```

Dispatch:

```bash
herdctl task --mission
```

### Task lifecycle

```text
IDLE -> ACTIVE -> COMPLETE
               -> ABORTED
               -> ERROR
```

Start:

```bash
herdctl task \
  'Implement X. Do not commit.' \
  --repo example-repo
```

Or:

```bash
herdctl task --mission --repo example-repo
```

Inspect:

```bash
herdctl task-status --repo example-repo
herdctl status --repo example-repo
```

Complete:

```bash
herdctl task-complete \
  --repo example-repo \
  --checkpoint-file .herd/state/task-checkpoint.md
```

Completed context is checkpointed to:

```text
.herd/memory/task-history.md
```

Long-running rejection/correction loops are intentional when they continue producing useful engineering evidence.

### Strict Reviewer protocol

The Reviewer contract requires exactly one canonical terminal decision:

```text
HERD_DECISION: APPROVE
```

or:

```text
HERD_DECISION: REJECT
```

Synonyms are not accepted.

After a Reviewer turn:

```bash
herdctl review-decision \
  --repo example-repo \
  --reviewer reviewer1
```

Example:

```json
{
  "valid": true,
  "decision": "APPROVE",
  "raw_token": "APPROVE",
  "round": 2,
  "review_file": "/.../.herd/state/reviews/<task>-round-02.md"
}
```

Malformed output produces:

```json
{
  "valid": false
}
```

The Lead re-prompts the same Reviewer session rather than interpreting malformed output itself.

### Idle-aware heartbeat

```text
15-minute tick
 |
 +-- no ACTIVE task ------------------> skip
 |
 +-- Supervisor working/blocked ------> skip
 |
 +-- ACTIVE + Supervisor idle/done ---> health-check
```

The heartbeat observes active work without inventing work when no task exists.

### Multi-repo usage

```bash
herdctl task 'Fix auth' --repo example-repo
herdctl task 'Improve onboarding' --repo another-repo
herdctl status --repo third-repo
```

Every repository receives isolated:

- Herdr workspace
- task state
- mission state
- context
- memory
- runtime configuration
- Git approval tokens

The Telegram Remote Operator preserves this same isolation: one configured repository per adapter instance, with session and approval state bound to that repository's resolved path.

### Target max-quality topology

```text
Human
 |
Phone / Terminal
 |
Codex Gateway or direct Codex CLI
 |
Codex Operator
 |
Herdr Handoff
 |
Claude Fable 5: Supervisor
 |
Claude Opus 5: Lead
 |
 +---------------------------------+
 | Adversarial Executor Pod        |
 |                                 |
 | Claude Fable 5: Executor        |
 |              ↕                  |
 | GPT-5.6 Sol High: Reviewer      |
 | Read-only validation role       |
 +---------------------------------+
 |
Lead verification
 |
Codex independent inspection
 |
Human commit gate
 |
Codex commit
 |
Human push / PR gate
 |
Codex delivery
 |
GitHub
```

The Reviewer remains read-only.

The deterministic harness/Lead persists review evidence; the Reviewer does not need write access to `.herd/state/`.

### Runtime command safety

Git itself permits bypass forms such as:

```bash
git push --no-verify
```

Runtime-level command protections and role contracts complement the deterministic Git guards.

The Codex Gateway preserves the operator boundary.

It does not become a replacement delivery authority.

### Direct Herdr mission workflow

A mission can still be explicitly created through the deterministic Herdr primitives:

```bash
herdctl mission create \
  "Fix the authentication bug" \
  --constraint "Do not change the database schema" \
  --rule "Preserve backward compatibility" \
  --acceptance "Authentication tests pass" \
  --verification "python3 -m unittest discover -s tests"
```

Inspect it:

```bash
herdctl mission show
```

Dispatch it:

```bash
herdctl task --mission
```

Herdr receives:

```text
OBJECTIVE
Fix the authentication bug

CONSTRAINTS
- Do not change the database schema

RULES
- Preserve backward compatibility

ACCEPTANCE CRITERIA
- Authentication tests pass

VERIFICATION
- python3 -m unittest discover -s tests
```

The Supervisor then owns engineering execution.

In normal operator use, however, humans should not need to manually construct this envelope.

Codex handles the translation from human intent into the Herdr handoff.

## 9. Telegram remote control

Telegram is the current reference transport for remote intent, plan approval,
status, and verified results. It ships as an MVP adapter (`telegram_operator/`
package, `tgop` entry script), and current `main` routes its transport
operations through the provider-neutral `HumanInteractionAdapter` seam using
`TelegramHumanInteractionAdapter`. It is an adapter, not an execution system,
and it has no direct path to Herdr or `herdctl`; the isolation is enforced by
the static suite. The broader target puts every human surface behind that
boundary and makes Grok Bot the preferred conversational plane; Telegram
remains the reference and the fallback until that is proven.

The authority model the adapter implements is in
[architecture.md](architecture.md#11-authority).

### Telegram remote operator experience

This is the released v0.6.3 (v0.1 adapter) experience for a mission
against the configured local repository; a remote target mission adds
the one-shot Approve Mission flow described under
[remote target routing](architecture.md#16-current-implementation-notes).

The implemented v0.1 interaction is deliberately simpler than the machinery underneath it.

You are away from the Mac.

You open Telegram on your phone.

You send:

```text
Fix issue #702 in the external target repository.

Investigate the actual cause, preserve the repository contribution rules,
add the necessary verification, and prepare the result for delivery.
```

Telegram forwards the intent to the trusted Mac.

Codex investigates.

Your phone receives:

```text
PLAN READY

Repository:
external-target

Objective:
Resolve issue #702

Constraints:
- preserve compatibility
- follow contribution rules
- add regression coverage
- no delivery without separate approval

Proceed?

[Approve]
[Reject]
```

You approve once. That one-shot decision authorizes only the exact plan presented for that Telegram user, private chat, repository, Gateway request, Codex session, Telegram message, and plan digest. Ordinary Telegram text grants no authority.

Then you can put the phone away.

Codex:

- starts/restores required local execution components
- creates the Herdr handoff
- dispatches Herdr
- monitors progress
- handles routine recovery
- creates bounded follow-up missions when necessary
- independently verifies the final result

Herdr:

- decomposes
- implements
- tests
- reviews
- rejects when necessary
- corrects
- re-reviews
- produces evidence

While the mission runs, your phone receives no unsolicited progress
delivery: v0.1 has no proactive progress streaming. (The one
unsolicited message class is a restart/recovery notice about work you
sent that was interrupted, if the adapter restarts mid-mission.) If
you want to know how things are going, you send:

```text
/status
```

The adapter acknowledges immediately, and the answer (durable
lifecycle state plus a read-only Operator status snapshot) arrives
when it reaches the front of the queue behind the active engineering
work.

When the engineering turn you approved completes, its reply is the
verified result:

```text
MISSION COMPLETE

Reviewer:
APPROVE

Tests:
Passed

Changed files:
4

Delivery:
Awaiting a separate human-controlled local Git authorization
```

Telegram v0.1 does not authorize or perform a commit, push, PR, tag, release, deployment, or merge. Those actions remain behind the existing explicit human gates outside the Telegram adapter.

Your Mac did the engineering.

Your phone was the intent, plan, status, and result control surface.

### Setup detail

Pinned by the `telegram_operator` suites.


Configuration lives OUTSIDE any repository, in
`~/Library/Application Support/DodgingInfinity/telegram/config.json`
(directory mode `700`, file mode `600`; the adapter refuses to load a
group/other-readable config because it holds the bot token):

```json
{
  "bot_token": "<token from @BotFather>",
  "allowed_user_ids": [123456789],
  "repository": "/path/to/one/repository"
}
```

- `allowed_user_ids` is an exact NUMERIC Telegram user-id allowlist.
- One repository per adapter instance.
- Durable adapter state (`state.json`) sits next to the config, also
  outside the repository, written atomically.

Run in the foreground with `tgop run`, or install the optional
per-user LaunchAgent with `tgop install-agent` (absolute paths,
RunAtLoad, KeepAlive with a restart throttle; logs in the protected
state directory, which the installer creates at mode `700`). An
explicit `--config PATH` given to `install-agent` is propagated into
the installed job as an absolute path, so the agent runs exactly the
named configuration, and the job's logs, state, and lock all live in
that config's directory (the default protected state directory
otherwise), never split across two locations. Because launchd does
not inherit your shell PATH, the installer resolves the `codex`
binary at INSTALL time and bakes its directory into the job's PATH
FIRST, ahead of a fixed constant list (never your ambient PATH), so
the exact validated binary always wins; installation refuses
with an actionable message when `codex` cannot be resolved, and the
agent must be reinstalled if the `codex` binary later moves. Disable
with `tgop uninstall-agent`; it unloads and removes exactly the
per-user plist the installer created. A single-instance lock refuses
a second concurrent adapter.

### Transport

The adapter uses genuine outbound Telegram Bot API long polling:
`getUpdates` with a positive server-side long-poll duration, and a
client socket deadline that is strictly LONGER than the long poll by a
hard constant margin (so the server always answers an idle poll before
the client gives up; a deadline firing on an idle poll is treated as a
normal empty poll, never an error and never a reason to disturb the
update offset). Failed polls recover with capped exponential backoff.
There is no webhook, no public listener, and no inbound port. These
deadlines exist ONLY in the Telegram transport; the Codex Gateway's
subprocess keeps its no-deadline behavior unchanged.

### Interaction

- Send natural-language intent (or `/mission <intent>`). The adapter
  authenticates the sender BEFORE parsing any content, then routes the
  intent through the Codex Gateway into a new or resumed Codex
  Operator session.
- The Operator answers through a versioned remote protocol envelope
  (plan / status / result / error). A free-form model message is never
  reinterpreted as an approved plan or a verified result.
- A `plan` reply is displayed first, with no controls; its one-shot
  **Approve / Reject** inline buttons are attached to the plan message
  only after complete delivery is proven and the exact message binding
  has been durably persisted.
- `/status` reports durable adapter lifecycle state first (the last
  gateway turn, queued items besides the status request itself,
  in-flight dispatch, approvable plans awaiting decision (counted
  across all chats; expired, consumed, and superseded approvals are
  excluded), and session-map evictions since first run, each an exact
  labelled count), then fetches engineering status through a
  separately constrained READ-ONLY Operator turn.
- `/status` is acknowledged immediately ("Gathering status…") but is
  ANSWERED through the same single worker that serializes every
  Gateway turn: a status request queues behind any active or
  already-queued Gateway work and is answered only when it reaches
  the front. Visibility is polled and lifecycle-based; there is no
  proactive progress streaming; send `/status` again for a fresh
  snapshot.
- `/help` (or `/start`) describes the commands.

### Approval binding

Plan approval is ONE-SHOT and bound to ALL of: the exact Telegram user
id, the private chat, the configured repository realpath, the Gateway
request, the Codex session, the Telegram plan message, the sha256
digest of the exact plan text, a random adapter-held nonce, and an
`expires_at` validity bound. No approval CONTROL exists before proof:
the plan text is sent with no keyboard, and the Approve / Reject
buttons are offered, on exactly the bound plan message, only after
the send outcome proves the complete plan text was displayed and the
message binding has been durably persisted. A plan too long to display
within the Telegram chunk cap is refused with no approval armed; a
truncated, partial, failed, or unverifiable plan delivery voids the
approval with an explanation and offers no buttons; and a failed or
unverifiable button offer voids the approval too; an actionable
approval binds exactly what was displayed, never undisplayed text. A
revised plan invalidates every prior
approval in the thread, and any intervening engineering turn in the
chat invalidates a still-pending approval at dispatch time (checking
/status does not). Replays, mismatches, expiry, and duplicates
all fail closed. The nonce never leaves the Mac: inline buttons carry
only an opaque approval id, and typed chat text can never forge the
adapter's decision envelope (marker-bearing user lines are visibly
quoted before forwarding).

### Recovery

Authority-bearing state is persisted before any external action, and
the Telegram offset advances only after accepted state is durably
stored. After a crash or restart the adapter reports
queued-but-undispatched work as dropped (re-send it) and
dispatched-but-unconfirmed work as AMBIGUOUS; it is never replayed
automatically.

### Remote mission result delivery (DI-REMOTE-2)

A remote target mission returns its verified
result to Telegram exactly once, by editing one bot-owned placeholder that
was bound before dispatch. The exact delivery contract, pinned by the
release narrative suite:

- Approval is **one-shot** and bound to the exact rendered mission
  text; a v2 approval dispatches no Codex turn — the Runtime, a
  separate process, claims the durably consumed authorization on its
  own. After Approve Mission there is **no manual Mac, clone,
  registration, Herdr-setup, configuration-switching, or terminal
  step**, and the lifecycle runs all the way to a **verified result
  returned to Telegram exactly once** (verified end to end by the
  hermetic release-narrative test). "Exactly once" means never twice and
  never silently dropped — NOT that it always eventually arrives: a
  placeholder-bound workflow can never fall back to a second result
  `sendMessage`, so the result is **not re-sent automatically** in any
  state. An ambiguous edit outcome (durable state `edit_indefinite`) is
  retried only as an idempotent edit of the same bound message; a result
  that does not fit one Telegram message (`degraded_unrenderable`), a
  bound placeholder that is gone or no longer matches (`degraded_unbindable`),
  and an ambiguous placeholder creation before dispatch (placeholder state
  `indefinite`) are terminal; `/status` says so, and recovering a terminal
  outcome is a human step. Records from the pre-DI-REMOTE-3 legacy lane
  (`reserved` / `partial`) are AT-MOST-ONCE and are never re-sent either.

### Delivery authority: local today, Telegram-native planned

Remote delivery authority is not implemented today. No Telegram
message, plain text or approval callback, can commit, push, open a
PR, tag, release, or deploy. The adapter's decision envelope states
explicitly that it grants no delivery authority. Commit, push, PR,
tag, and release remain separate, human-authorized, local actions.

Telegram-native delivery authorization is now a Phase-I product requirement.
It will require exact mission/result/diff/ref binding, expiring one-shot human
approval, durable receipts, replay protection, and separate authorization at
each commit, push, PR, tag, release, deploy, or merge boundary. The design is
tracked on the [roadmap](roadmap.md).

### Telegram security requirements

The adapter enforces, with static and behavioral regression tests:

- allowlist trusted NUMERIC Telegram user IDs, private chats only
- authenticate every update BEFORE parsing or persisting content
- reject unknown senders with no reply; nothing they send is parsed
  or persisted: no content, intent, approval, work, or session
  state. The only durable effect of a denied update is the transport
  update-offset advance (intended poll-loop bookkeeping, so a denied
  update is not re-fetched and cannot wedge the poller)
- never expose arbitrary shell execution
- never forward raw shell commands directly
- never invoke Herdr, never read orchestration state, not even a
  path string (isolation is enforced by the static suite)
- never bypass Codex
- never silently interpret chat text as Git authorization
- bind approval actions to a known Codex request/session and the
  exact bounded plan
- redact the bot token from every error and diagnostic surface

## 10. Runtime, host, and the Codex Gateway

### Runtime service

The DI-REMOTE-2 Runtime is the `target_runtime/`
package with the `dirun` entry script: a separate local process coupled to
the control chain only through the durable workflow authority store. It
claims authorized workflows and advances the full lifecycle; Telegram, the
Gateway, and Codex never invoke it in-process. Its contract and its proof
boundary are in
[architecture.md](architecture.md#16-current-implementation-notes).
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

### Host requirements

The trusted Mac is the execution node. Remote
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

### What survives a restart today

Pinned by the adapter and Runtime suites.

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

### Known limits

Host survival is Phase 1 work. Cold reboot, login
ordering, sleep and wake, long sleep, network loss and recovery, DNS
failure, and temporary GitHub, Telegram, or model outages have not been
validated as a matrix; the
[roadmap](roadmap.md)
tracks them under Iteration 0 and Iteration 8. Break-glass access over
Tailscale and SSH, service identity that exposes running versus on-disk
commit, and a readiness dependency graph are on the same list. A stale
process should never look healthy simply because it is still running;
today that detection is manual. The
[architecture](architecture.md#12-observation-reconciliation-and-recovery)
page describes the target recovery classes and the
[roadmap](roadmap.md) places them in Phase 1 and Phase 2.

### The Codex Gateway

Codex Gateway v0.1 (released in v0.6.2) is the
local, transport-neutral interface boundary in front of the current
reference Operator, which is Codex. In this checkout the Gateway is reached
through the provider-neutral `OperatorSession` seam described in
[architecture.md](architecture.md#6-operator-and-model-routing); `CodexOperatorSession`
resolves the Gateway's `build_request` and `submit` at call time. The
target design replaces the Operator behind that seam. Codex remains the
reference implementation and the fallback until another path is proven.

Codex Gateway v0.1 adds a local, transport-neutral interface boundary in front of the existing Codex Operator workflow.

Its job is intentionally narrow:

```text
Human intent
    |
    v
codexgw
    |
    v
Codex Gateway
    |
    v
Local Codex CLI
    |
    v
AGENTS.md + OPERATOR_PROTOCOL.md
    |
    v
Existing Codex Operator workflow
    |
    v
Herdr only when Codex decides to dispatch engineering work
```

Gateway v0.1 provides:

- **Versioned request/response contracts**
- **Local terminal entry point**
- **New Codex Operator sessions**
- **Resumed Codex Operator sessions**
- **Repository/operator-contract validation**
- **Subprocess argument isolation**
- **Fail-closed structured output**
- **Strict UTF-8 boundaries**
- **Bounded errors**
- **Hermetic regression coverage**
- **Static enforcement of the gateway/Herdr architectural boundary**

The gateway invokes the installed Codex CLI.

It never becomes an engineering runtime.

#### Isolation

Gateway source must not:

- import `herdr`
- call `HerdrControlPlane`
- invoke `herdctl`
- manipulate `.herd`
- construct mission envelopes
- prompt Supervisor, Lead, Executor, or Reviewer
- grant execution approval
- perform Git delivery

Engineering remains downstream of Codex.

#### Non-goals

Gateway v0.1 intentionally does not provide:

- remote networking
- Telegram
- authentication
- a daemon
- HTTP
- sockets
- queues
- mission construction
- Herdr dispatch
- Herdr lifecycle management
- commit/push/tag/release authority

Those responsibilities remain outside the Gateway itself. The Telegram adapter supplies Telegram transport, allowlist authentication, and the optional long-running LaunchAgent process as a client of the Gateway; it does not add those capabilities to the Gateway. Mission construction, Herdr dispatch and lifecycle management, and delivery authority remain downstream or separately deferred.

#### Live compatibility validation

Gateway tests remain hermetic and mock Codex rather than consuming a real Codex engineering turn.

Before the Telegram adapter was enabled, the installed Codex CLI was separately checked against the declared compatibility boundary. That validation covered:

- new Codex sessions work
- resumed Codex sessions work
- `AGENTS.md` is loaded from the target repository
- `OPERATOR_PROTOCOL.md` remains authoritative
- clarification responses round-trip correctly
- approval requests round-trip correctly
- real Codex structured events match the gateway parser
- malformed/unexpected events fail closed
- no direct Herdr path exists through the gateway

#### The Codex Gateway command surface

The Codex Gateway is a separate outer interface.

Installed CLI:

```text
codexgw
```

Inspect installed options:

```bash
codexgw --help
```

The gateway intentionally remains separate from `herdctl`.

That separation is architectural, not cosmetic.

## 11. Git approval gates

Three deterministic one-shot human authorization gates protect commit, branch
push, and release-tag push. They are enforced by the installed Git guards and
pinned by the Herdr guard suites. The authority model they implement is in
[architecture.md](architecture.md#11-authority).

### Why the gates are separate

Each gate authorizes exactly one action against exactly one state. A
commit approval binds a staged diff; it says nothing about pushing the
resulting commit. A push approval binds a commit SHA and a remote ref; it
says nothing about a tag. A tag approval binds one annotated tag object.
No approval inherits from another, none is reusable, and each carries a
short TTL. The chain is deliberately broken at every link: mission
authorization does not authorize a commit, a commit does not authorize a
push, a push does not authorize a merge, a merge does not authorize a
release, and a release does not authorize deployment.

Remote missions carry `delivery_authority = none`. No Telegram message,
plain text or approval callback, can operate any of these gates today.
Exact, one-shot Telegram-native delivery approvals are a Phase-I
requirement in the
[roadmap](roadmap.md), not
implemented behavior.

### Human commit gate

Stage the exact desired result.

Then:

```bash
herdctl approve-commit --repo example-repo
```

Authorization is bound to:

- repository/worktree
- branch
- HEAD
- exact staged diff hash
- short TTL

A changed staged diff, branch, or HEAD invalidates the approval.

For Codex Operator flows after explicit human confirmation:

```bash
herdctl approve-commit --yes
```

Codex may then execute the exact commit.

### Human push gate

Inspect:

```bash
git status -sb
git log --oneline origin/main..HEAD
```

Authorize:

```bash
herdctl approve-push --repo example-repo
```

Then:

```bash
git push
```

A dry run does not consume approval:

```bash
git push --dry-run
```

Push remains independently authorized from commit.

### Human release-tag gate

Create an annotated tag:

```bash
git tag -a vX.Y.Z -m "Dodging Infinity vX.Y.Z"
```

Authorize exactly that tag:

```bash
herdctl approve-push --tag vX.Y.Z
```

Push:

```bash
herdctl push-tag vX.Y.Z
```

Authorization binds to the exact tag ref and object.

### What a gate does not authorize

- A commit approval does not authorize a push, a PR, a tag, a release, a
  deployment, or a merge.
- A push approval does not authorize a tag push, a merge, a release, or a
  deployment. `git push --dry-run` does not consume it.
- A tag approval authorizes exactly one tag ref and tag-object SHA. It does
  not authorize a release or a deployment.
- No gate can be operated by an agent, a transport, or a UI on its own.
  `herdctl approve-commit --yes` exists for Operator flows and is used only
  after explicit human confirmation.
- Git itself permits bypass forms such as `git push --no-verify`.
  Runtime-level command protections and role contracts complement the
  deterministic Git guards; they do not replace human authorization.

## 12. Troubleshooting

Start with the read-only commands. None of them repairs anything, and none of
them can make a situation worse.

| Symptom | First check | Notes |
|---|---|---|
| `herdctl` not found | `export PATH="$HOME/.local/bin:$PATH"` | `scripts/install.sh` writes the wrappers there. |
| A repository behaves as if it has no Herdr | `herdctl doctor --repo NAME` | Reports missing binaries, runtime kinds, and uninstalled Git guards. |
| Agents look wrong or absent | `herdctl health --repo NAME` | A nonzero exit names the missing or malformed infrastructure. `idle` and `blocked` are work states, not failures. |
| You cannot tell what the herd is doing | `herdctl status --repo NAME`, then `herdctl observe --repo NAME` | `observe` is the full bounded projection; `--json` gives the machine form. |
| Observation says `PARTIAL` | Read the `completeness` field and the diagnostics | Completeness describes visibility, not health. An agents-unprobed global PARTIAL is expected in production. |
| A Reviewer turn produced no usable decision | `herdctl review-decision --repo NAME --reviewer reviewer1` | `{"valid": false}` means the Lead re-prompts the same Reviewer session rather than interpreting the output. |
| `/status` reports the Runtime as not running | `dirun once`, or `scripts/dirun-agent.sh install` | A stopped or uninstalled Runtime is an actionable error naming the exact remedy commands, never a silent stall. |
| Work you sent before a restart is missing | Re-send it | After a crash the adapter reports queued-but-undispatched work as dropped and dispatched-but-unconfirmed work as AMBIGUOUS. It is never replayed automatically. |
| A result never arrived | `/status` | Terminal delivery states (`degraded_unrenderable`, `degraded_unbindable`, placeholder `indefinite`) are disclosed rather than retried. Recovering one is a human step. |
| The adapter refuses to start | Check the config file mode | The directory must be `700` and the file `600`; a group- or other-readable config is refused because it holds the bot token. |
| A v1 state file after an upgrade | `tgop migrate-state`, `tgop migrate-workflows` | State fails closed at adapter startup until the human runs the migration. v1 workflow records are retired, never upgraded. |
| The LaunchAgent runs the wrong `codex` | Reinstall the agent | The installer resolves `codex` at install time and bakes its directory into the job's PATH first. The agent must be reinstalled if the binary moves. |
| A commit or push approval will not apply | Re-stage, then re-approve | Each approval binds exact state and has a short TTL. A changed staged diff, branch, or HEAD invalidates it. |

Cold reboot, login ordering, sleep and wake, long sleep, network loss and
recovery, DNS failure, and temporary GitHub, Telegram, or model outages have
not been validated as a matrix. A stale process should never look healthy
simply because it is still running; today that detection is manual. Those are
open items on the [roadmap](roadmap.md).

## 13. Command reference

### `herdctl`

```text
herdctl init [--alias NAME] [--preset PRESET] [--test-command COMMAND]

herdctl presets
herdctl preset PRESET --repo NAME
herdctl set-test COMMAND --repo NAME

herdctl upgrade --repo NAME [--preset PRESET]
herdctl repos [--prune]

herdctl safety-install
herdctl doctor --repo NAME
herdctl health --repo NAME
herdctl integrations --repo NAME

herdctl bootstrap --repo NAME [--force]

herdctl mission create "OBJECTIVE" \
  [--repo NAME] \
  [--constraint CONSTRAINT ...] \
  [--rule RULE ...] \
  [--acceptance CRITERION ...] \
  [--verification COMMAND ...]

herdctl mission show [--repo NAME]

herdctl task "..." \
  --repo NAME \
  [--rule RULE ...] \
  [--rejection-drill]

herdctl task --mission \
  --repo NAME \
  [--rule RULE ...] \
  [--rejection-drill]

herdctl task-status --repo NAME
herdctl task-complete --repo NAME --checkpoint-file FILE
herdctl task-abort --repo NAME [--reason REASON]

herdctl rules --repo NAME
herdctl rules add RULE --repo NAME
herdctl rules remove RULE --repo NAME

herdctl review-decision --repo NAME --reviewer reviewer1

herdctl clear-contexts --repo NAME

herdctl approve-commit --repo NAME
herdctl approve-commit --repo NAME --yes

herdctl approve-push \
  --repo NAME \
  [--remote origin] \
  [--target-branch BRANCH]

herdctl approve-push \
  --repo NAME \
  [--remote origin] \
  --tag TAG

herdctl push-tag TAG --repo NAME

herdctl status --repo NAME
herdctl observe --repo NAME [--json]
herdctl read ROLE --repo NAME
herdctl prompt ROLE "..." --repo NAME

herdctl heartbeat --once --repo NAME
herdctl restart-heartbeat --repo NAME
```

### `tgop`

```text
tgop [--config PATH] run
tgop [--config PATH] install-agent
tgop [--config PATH] uninstall-agent
tgop [--config PATH] migrate-state
tgop [--config PATH] migrate-workflows
```

### `dirun`

```text
dirun [--config PATH] once
dirun [--config PATH] run

scripts/dirun-agent.sh install [--config PATH]
scripts/dirun-agent.sh uninstall
```

### `codexgw`

```text
codexgw --help
```

The Codex Gateway is a separate outer interface and intentionally remains
separate from `herdctl`. That separation is architectural, not cosmetic.

### Telegram

The Telegram command surface is in section 9.
