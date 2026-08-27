<p align="center">
  <img src="assets/brand/banner.svg" alt="Dodging Infinity — Bounding the infinite to the finite." width="100%">
</p>

# Dodging Infinity

[![CI](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml/badge.svg)](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheMickeyDodger/dodging-infinity)](https://github.com/TheMickeyDodger/dodging-infinity/releases/latest)

> **Bounding the infinite to the finite.**

**Any problem. Any repo. Any issue.**

Dodging Infinity is a local-first autonomous engineering orchestration system that turns human intent into bounded, isolated, independently reviewed engineering work.

The system separates four responsibilities deliberately:

- the **human** defines intent and retains delivery authority
- **Codex** defines the destination and mission boundary
- **Herdr Supervisor** determines the engineering route
- **Lead, Executor, and Reviewer** perform and challenge the engineering work

The core principle is simple:

> **CODEX DEFINES THE DESTINATION.**  
> **HERDR SUPERVISOR DETERMINES THE ENGINEERING ROUTE.**

---

## Current status

| Surface | Status |
|---|---|
| Latest tagged release | **v0.6.3** |
| Current `main` | **DI-REMOTE-2 Remote Target Repository Routing** |
| Remote Telegram MVP | Released in v0.6.3 |
| Cross-repository routing | Implemented on `main` |
| DI-REMOTE-2 validation | Hermetic + CI validated |
| Live cross-repository Telegram validation | **Pending** |
| Remote Git delivery authority | **Not implemented by design** |

v0.6.3 remains the latest tagged release.

Current `main` contains the unreleased **DI-REMOTE-2 Remote Target Repository Routing** architecture. It allows a human to authorize one exact bounded mission against a remote GitHub repository while Dodging Infinity remains the permanent control and policy repository.

The complete external flow has been tested hermetically, but the first real end-to-end target dispatch against a live external repository is still pending.

---

# What Dodging Infinity does

Most agent systems try to make one model do more.

Dodging Infinity takes the opposite approach:

**make the problem smaller, bound the authority, and separate responsibilities.**

A human request becomes:

1. a bounded Mission Authorization
2. a deterministic target lifecycle
3. an isolated target workspace
4. a Herdr engineering mission
5. adversarial implementation and review
6. independent evidence-based verification
7. a result returned to the human
8. a separate human-controlled delivery decision

The intelligence is replaceable.

**The orchestration contract is not.**

---

# Current architecture on `main`

DI-REMOTE-2 is the principal architecture on current `main`.

Dodging Infinity remains permanently pinned as the **control and policy repository**.

The repository being engineered is a separate **target repository**.

```text
Human
  |
  v
Phone / Telegram
  |
  v
Telegram Adapter
  |
  v
Legacy Codex Gateway routing turn
  |
  |  DI-REMOTE-2 marker = routing signal only
  |  no mission authority
  v
Fresh restricted Codex planning turn
  |
  v
Mission Authorization
  |
  v
Human: Approve Mission / Reject Mission
  |
  v
Durable workflow authority
  |
  v
Runtime (`dirun`)
  |
  v
Target Broker
  |
  v
Managed isolated target workspace
  |
  v
Fresh handoff-validation Codex turn
  |
  v
Byte-exact bounded mission handoff
  |
  v
Target Herdr
  |
  v
Supervisor
  |
  v
Lead
 /   \
v     v
Executor  Reviewer
 \     /
  \   /
   v
Engineering result
  |
  v
Runtime observation
  |
  v
Fresh evidence-based Codex verification
  |
  v
Verified result
  |
  v
Telegram
  |
  v
Separate human delivery gate
```

The trusted Mac remains the execution node.

Telegram is a remote control surface, not an execution environment.

---

# Ownership model

## Human

The human owns:

- original intent
- Mission Authorization approval
- rejection of an incorrect mission boundary
- commit approval
- push approval
- PR publication approval
- tag approval
- release approval
- deployment approval
- merge approval

An engineering approval is **not** a delivery approval.

---

## Codex Operator

Codex owns the human-to-engineering boundary.

Codex may:

- interpret human intent
- investigate the requested repository or issue
- resolve the canonical target
- establish the proposed baseline
- define the objective
- define constraints and rules
- define the desired outcome
- define acceptance criteria
- identify unresolved questions
- create the bounded Mission Authorization
- request preparation
- determine whether the approved mission remains permissible after target rules are loaded
- request dispatch
- evaluate final engineering evidence
- request bounded corrective follow-up when acceptance is not satisfied
- request reauthorization when the approved boundary is no longer sufficient

Codex does **not** choose:

- files to edit
- source architecture
- implementation strategy
- engineering decomposition
- technical sequencing
- role assignments
- Reviewer strategy
- remediation strategy

Codex does not become Supervisor.

---

## Runtime

The Remote Workflow Runtime (`dirun`) is deterministic.

It owns:

- durable workflow state
- fixed lifecycle transitions
- fresh Codex role scheduling
- closed outcome validation
- one-shot internal capability issuance
- deterministic Broker invocation
- target observation scheduling
- verification scheduling
- bounded recovery scheduling

It cannot:

- reinterpret human intent
- invent engineering work
- broaden mission scope
- choose implementation strategy
- execute arbitrary shell commands
- commit
- push
- open PRs
- tag
- release
- deploy
- merge

---

## Target Broker

The Broker performs privileged but fixed target lifecycle actions.

Sensitive values are resolved from protected workflow state, never supplied by Telegram or Codex.

The Broker owns:

- managed workspace materialization
- origin verification
- approved baseline checkout
- target instruction discovery
- exact handoff dispatch
- target Herdr bootstrap
- read-only target observation
- bounded evidence collection
- bounded corrective dispatch
- dispatch reconciliation
- completion
- workspace release primitives

It has no arbitrary-command API.

---

## Herdr Supervisor

The Supervisor is the **first engineering strategy-bearing component**.

Supervisor owns:

- engineering planning
- decomposition
- technical approach
- sequencing
- role assignment
- validation strategy

This boundary is intentional.

> **Codex defines what must be accomplished.  
> Supervisor decides how engineering accomplishes it.**

---

## Lead

Lead owns:

- translating Supervisor strategy into bounded work
- Executor coordination
- Reviewer coordination
- independent pre-review verification
- acceptance
- completion evidence

---

## Executor

Executor implements the engineering work.

---

## Reviewer

Reviewer independently and adversarially evaluates the result.

The Reviewer must produce exactly one canonical decision:

```text
HERD_DECISION: APPROVE
```

or:

```text
HERD_DECISION: REJECT
```

Rejection returns work to the engineering loop.

There is no artificial review-round limit.

---

# Mission Authorization

DI-REMOTE-2 does not ask the human to approve an engineering plan.

It asks the human to approve the **mission boundary**.

Conceptually:

```text
MISSION AUTHORIZATION

CONTROL
Dodging Infinity

TARGET
https://github.com/example/project

REFERENCE
issue #123

BASELINE
main @ <exact approved SHA>

ORIGINAL REQUEST
> Fix the issue and verify the result.

OBJECTIVE
> Resolve issue #123.

CONSTRAINTS
> Preserve compatibility.
> Follow applicable repository rules.

RULES
> Dodging Infinity control authority remains controlling.

DESIRED OUTCOME
> The issue is resolved and independently verified.

ACCEPTANCE
> Relevant tests pass.
> Herdr Reviewer approves.
> Codex independently verifies the evidence.

UNRESOLVED QUESTIONS
> none

EXECUTION SCOPE
> Engineering execution only.

DELIVERY AUTHORITY
none

[Approve Mission] [Reject Mission]
```

Approving means:

> **This exact target, baseline, objective, scope, constraints, and acceptance boundary are what I intend.**

It does **not** mean:

> Use this architecture, edit these files, or follow this implementation plan.

Implementation strategy belongs to Herdr Supervisor.

---

# Remote target lifecycle

After Mission Authorization is consumed:

```text
AUTHORIZED
  |
  v
fresh execution_prepare Codex turn
  |
  v
request_prepare
  |
  v
Runtime validation
  |
  v
Broker workspace materialization
  |
  v
origin + baseline verification
  |
  v
target instruction discovery
  |
  v
preparation receipt
  |
  v
fresh handoff-validation Codex turn
  |
  +--> request_dispatch
  +--> needs_reauthorization
  +--> blocked
  |
  v
Runtime dispatch validation
  |
  v
Broker dispatch
  |
  v
target Herdr
  |
  v
Supervisor engineering plan
```

The initial target dispatch uses the **byte-exact stored bounded handoff**.

The Broker does not rewrite it.

The Runtime does not rewrite it.

Codex does not insert implementation strategy into it.

---

# Managed target workspaces

Remote targets are materialized into isolated managed workspaces.

The system does not search for or adopt arbitrary user working copies.

The Broker verifies:

- canonical GitHub identity
- approved repository origin
- exact approved baseline SHA
- detached target HEAD
- clean initial workspace
- workspace containment
- control repository separation

A changed target, baseline, origin, revision, policy, or workspace fails closed.

Target paths are derived from protected workflow state.

Telegram and Codex do not supply local filesystem paths.

---

# Target instruction handling

Before dispatch, the Broker reads a bounded root instruction allowlist:

- `AGENTS.md`
- `CONTRIBUTING.md`
- `README.md`

Target-authored content is treated as subordinate, untrusted repository data.

Instruction reads refuse unsafe filesystem behavior including:

- symlink traversal
- hardlink substitution
- FIFOs
- devices
- non-regular files
- containment escape
- oversized content
- invalid UTF-8

Target instructions cannot structurally alter:

- control authority
- target identity
- baseline
- mission scope
- lifecycle vocabulary
- Broker capability
- delivery authority

Path-scoped nested instruction discovery remains a future hardening item and is not claimed as live-complete today.

---

# Fresh Codex role turns

DI-REMOTE-2 does not rely on persistent Codex thread authority.

Every authority-bearing role turn is a fresh process.

The production posture is intentionally restrictive:

- read-only sandbox
- no `codex exec resume`
- no `--add-dir`
- no workspace write access
- no `danger-full-access`
- no `approve-for-me`
- ignored ambient user configuration
- ignored ambient rules
- `approval_policy=never`

Codex receives no Broker capability and no writable target path.

Codex decides.

Runtime acts.

Broker performs fixed privileged lifecycle operations.

---

# Verification

Herdr `COMPLETE` is **never sufficient** to produce a verified result.

After the target task stops, Runtime/Broker collect a bounded objective evidence projection.

The fresh verification Codex turn receives evidence including, where available:

- workflow identity and revision
- canonical target
- issue or PR identity
- approved baseline
- live target origin
- live target HEAD
- changed-path inventory
- staged and untracked state
- bounded diff
- full-diff digest
- Herdr task status
- task checkpoint
- canonical Reviewer identity
- Reviewer round
- Reviewer decision
- Reviewer evidence
- recorded tests
- recorded mutation evidence
- approved acceptance criteria
- control-policy comparison
- protected-control-surface evidence
- structural `delivery_authority: none`
- dispatch receipt
- handoff digest

Target-authored evidence is data, not authority.

The verification turn may return only:

```text
verified_result
request_follow_up
needs_reauthorization
blocked
```

Even if Codex returns `verified_result`, the Broker re-collects fresh evidence and independently re-applies structural gates before the workflow may become `VERIFIED`.

Required conditions include:

- evidence is complete and valid
- target origin still matches
- target HEAD remains the approved baseline
- target Herdr task is stopped
- canonical Reviewer decision is `APPROVE`
- control policy still matches
- protected control surfaces still match
- `delivery_authority` is still exactly `none`

A lifecycle `COMPLETE` flag by itself can never authorize success.

---

# Bounded corrective follow-up

A failed acceptance criterion does not automatically require a new human authorization.

Codex may request bounded corrective follow-up when the existing authorization still covers the same:

- target
- baseline
- objective
- material scope
- constraints
- acceptance boundary
- delivery authority

The corrective handoff contains the failed acceptance evidence and corrective objective.

It does **not** contain a technical solution.

Herdr Supervisor again determines the corrective engineering route.

Material scope change requires a new Mission Authorization.

---

# Dispatch ambiguity recovery

External effects are never blindly replayed.

If a durable dispatch receipt exists but target identity cannot be reconciled, Runtime may schedule a fresh `status_recovery` Codex turn.

Codex may return only:

```text
request_recovery
blocked
```

`request_recovery` maps to the fixed Broker operation:

```text
reconcile_dispatch
```

Reconciliation may:

1. inspect existing Herdr child/control-plane evidence
2. bind exactly one provably matching existing target task
3. otherwise transition durably to `BLOCKED`

It may **never**:

- spawn again
- redispatch
- restart
- replay the original external action
- execute arbitrary recovery commands

The system prefers a visible blocked mission over duplicate engineering execution.

---

# Telegram Remote Operator

Telegram is the first remote client.

The adapter lives in:

```text
telegram_operator/
```

CLI:

```text
tgop
```

Telegram remains transport only.

It never directly invokes:

- Runtime
- Broker
- Herdr
- `herdctl`

---

## Telegram configuration

Configuration lives outside the repository:

```text
~/Library/Application Support/DodgingInfinity/telegram/config.json
```

Example:

```json
{
  "bot_token": "<token from @BotFather>",
  "allowed_user_ids": [123456789],
  "repository": "/path/to/dodging-infinity"
}
```

The configured repository is the permanent control repository.

For DI-REMOTE-2, the target repository is resolved per mission.

The user does **not** need to:

- know a target clone path
- change Telegram configuration
- manually clone the target
- register the target with Herdr
- open a target terminal
- manually start target agents

---

## Telegram commands

```text
/help
/start
/status
/mission <intent>
```

Natural-language intent may also be sent directly.

Example:

```text
I want to solve https://github.com/example/project/issues/123. Go do it.
```

---

## Telegram approval binding

Mission approval is one-shot.

Approval is bound to the exact:

- Telegram user
- private chat
- control repository
- workflow
- revision
- rendered Mission Authorization
- Telegram message
- approval expiry
- consumed state

The mission is sent without controls first.

Approve / Reject controls are attached only after complete delivery and durable message binding are proven.

Partial, truncated, ambiguous, or unverifiable delivery gets no actionable approval control.

---

## Telegram `/status`

`/status` is deterministic and store-based.

It reports known durable facts such as:

- workflow phase
- target
- issue or PR
- target task status
- observation state
- result state
- delivery state
- Runtime liveness
- recovery state

Healthy status polling does not require a model turn.

There is no proactive progress streaming.

Send `/status` again for a fresh snapshot.

---

## Result delivery

A completed verified result is returned to the bound Telegram chat.

Delivery is designed as **never twice**, not guaranteed eventual delivery across every crash window.

States distinguish:

- pending
- reserved / attempted but unconfirmed
- partial
- delivered

Ambiguous or partial result delivery is not automatically resent.

`/status` exposes the condition so the human can recover deliberately.

---

# Released v0.6.3 compatibility path

v0.6.3 shipped the original Telegram Remote Operator MVP for missions against the configured local repository.

That compatibility path remains available on `main`.

```text
Phone
  |
  v
Telegram
  |
  v
Telegram Adapter
  |
  v
Codex Gateway
  |
  v
Codex Operator
  |
  v
Herdr
  |
  v
verified result
  |
  v
human delivery gate
```

The released v0.6.3 adapter supports:

- real Telegram Bot API traffic
- trusted numeric user allowlisting
- private-chat enforcement
- natural-language intent
- new and resumed Codex sessions
- one-shot plan approval / rejection
- `/status`
- verified-result delivery
- optional macOS LaunchAgent operation
- restart recovery
- no direct Telegram path to Herdr or `herdctl`

v0.6.3 does **not** include the current DI-REMOTE-2 cross-repository architecture.

---

# Codex Gateway

The Codex Gateway is a transport-neutral interface in front of Codex.

CLI:

```text
codexgw
```

It:

- accepts intent
- validates repository/operator context
- starts or resumes Codex for the released local path
- returns structured responses
- preserves source identity
- fails closed on malformed output

It does **not**:

- import Herdr
- invoke `HerdrControlPlane`
- invoke `herdctl`
- construct Herdr missions
- prompt Herdr agents
- dispatch engineering work
- grant delivery authority

The Gateway is an interface boundary, not an engineering runtime.

---

# Runtime service

DI-REMOTE-2 uses the separate deterministic Runtime:

```text
dirun
```

Install the wrapper:

```bash
scripts/install.sh
```

One pass:

```bash
dirun once
```

Foreground loop:

```bash
dirun run
```

Optional macOS LaunchAgent:

```bash
scripts/dirun-agent.sh install
scripts/dirun-agent.sh install --config PATH
scripts/dirun-agent.sh uninstall
```

The Runtime uses:

- one-shot capabilities
- protected workflow state
- a single-instance lock
- fixed Broker actions
- fresh restricted Codex role turns

---

# Direct Herdr workflow

Dodging Infinity can still be used directly without Telegram.

Create a mission:

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

Dispatch:

```bash
herdctl task --mission
```

Herdr receives the bounded mission.

Supervisor then determines the engineering route.

---

# Repository setup

Initialize a repository:

```bash
herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'npm test && npm run build'
```

Or configure verification later:

```bash
herdctl init --alias my-repo --preset max-quality
herdctl set-test 'npm test && npm run build' --repo my-repo
```

Upgrade an existing Herdr repository:

```bash
herdctl upgrade --repo example-repo
herdctl doctor --repo example-repo
```

---

# Presets

List presets:

```bash
herdctl presets
```

Built-ins include:

```text
max-quality
all-claude
conservative
```

Apply:

```bash
herdctl preset max-quality --repo example-repo
```

Presets assign models and runtimes.

They do not change the orchestration hierarchy.

---

# Model and runtime agnosticity

Dodging Infinity separates roles from models.

```text
Supervisor -> Model A
Lead       -> Model B
Executor   -> Model C
Reviewer   -> Model D
```

or:

```text
Supervisor -> Model X
Lead       -> Model X
Executor   -> Model X
Reviewer   -> Model X
```

The orchestration contract belongs to the role.

The model is replaceable.

A high-quality topology may look like:

```text
Human
  |
Codex Operator
  |
Herdr
  |
Supervisor
  |
Lead
 /  \
Executor Reviewer
 \  /
  v
Lead acceptance
  |
Codex independent verification
  |
Human delivery gate
```

---

# Observability

Dodging Infinity separates reporting from control.

## `herdctl status`

```bash
herdctl status --repo example-repo
```

Answers:

> What is the Herdr currently doing?

---

## `herdctl health`

```bash
herdctl health --repo example-repo
```

Strictly read-only.

Checks:

- Herdr configuration
- server reachability
- runtime state
- expected/live agents
- task-state readability

Healthy:

```text
Health: READY
```

---

## `herdctl observe`

```bash
herdctl observe --repo example-repo
herdctl observe --repo example-repo --json
```

Builds a bounded point-in-time projection of:

- repository
- config
- mission
- task
- runtime
- agents
- children
- reviews
- artifacts
- recent tasks
- diagnostics

Observation does not:

- mutate
- repair
- prompt
- recover
- control execution

It is an instrument panel.

---

# Task lifecycle

```text
IDLE
  |
  v
ACTIVE
  |
  +--> COMPLETE
  +--> ABORTED
  +--> ERROR
```

Inspect:

```bash
herdctl task-status --repo example-repo
herdctl status --repo example-repo
```

Completion requires a persisted checkpoint:

```bash
herdctl task-complete \
  --repo example-repo \
  --checkpoint-file .herd/state/task-checkpoint.md
```

Completed context is retained in:

```text
.herd/memory/task-history.md
```

Long rejection/correction loops are allowed when they continue producing meaningful engineering evidence.

---

# Idle-aware heartbeat

```text
15-minute tick
 |
 +-- no ACTIVE task ------------------> skip
 |
 +-- Supervisor working/blocked ------> skip
 |
 +-- ACTIVE + Supervisor idle/done ---> health-check
```

Heartbeat observes and recovers existing work.

It does not invent new work.

---

# Human delivery gates

Engineering authority and delivery authority are intentionally separate.

## Commit

Stage the exact result:

```bash
herdctl approve-commit --repo example-repo
```

Authorization binds to:

- repository
- branch
- HEAD
- exact staged diff
- TTL

Changed staged content invalidates approval.

---

## Push

Authorize separately:

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

---

## Release tag

Create the annotated tag:

```bash
git tag -a vX.Y.Z -m "Dodging Infinity vX.Y.Z"
```

Authorize:

```bash
herdctl approve-push --tag vX.Y.Z
```

Push:

```bash
herdctl push-tag vX.Y.Z
```

Commit, push, PR, tag, release, deployment, and merge remain separately human-authorized operations.

---

# Rules and constraints

Repository rules:

```bash
herdctl rules --repo example-repo

herdctl rules add \
  "Never modify migrations" \
  --repo example-repo

herdctl rules remove \
  "Never modify migrations" \
  --repo example-repo
```

Task-local rule:

```bash
herdctl task "Implement X" \
  --repo example-repo \
  --rule "Only modify README.md" \
  --rule "Do not commit"
```

Rules remain scoped to the repository or mission that owns them.

---

# Security model

Dodging Infinity is designed around explicit authority boundaries.

Key properties include:

- Telegram authenticates before parsing user content
- private-chat allowlisting
- no arbitrary shell over Telegram
- no Telegram direct Herdr path
- no Gateway direct Herdr path
- no supported Codex direct Broker/Herdr path in DI-REMOTE-2
- fresh read-only Codex role turns
- one-shot capabilities
- exact target/baseline/revision binding
- byte-exact initial mission handoff
- managed target workspace containment
- fail-closed target identity validation
- unsafe target-instruction reads rejected
- v1 approvals cannot authorize v2 missions
- verification requires objective evidence
- Herdr `COMPLETE` alone cannot verify a mission
- ambiguous dispatch effects are never blindly replayed
- delivery authority is structurally `none`

The current trusted-node model assumes the local macOS user and protected filesystem are inside the trust boundary.

Workflow records are digest-bound and permission-protected, not cryptographically signed.

See [SECURITY.md](SECURITY.md) for the full threat model and known limitations.

---

# What is proven today

## Released and live-proven

v0.6.3 has been exercised with:

- real Telegram Bot API traffic
- allowlisted private users
- natural-language intent
- new and resumed Codex sessions
- approval / rejection callbacks
- live inline-button attachment
- `/status`
- verified-result delivery
- background LaunchAgent operation
- process restart recovery
- short network-loss recovery
- sleep / wake recovery

---

## Implemented and hermetically proven on `main`

DI-REMOTE-2 has automated coverage for:

- exact Mission Authorization rendering
- one-shot approval
- canonical target resolution
- managed workspace materialization
- origin / baseline validation
- target instruction discovery
- fresh handoff-validation turns
- byte-exact target handoff
- Supervisor-first strategy boundary
- Runtime lifecycle
- dispatch reconciliation
- no-replay recovery
- objective engineering evidence collection
- Reviewer evidence
- evidence-based verification
- bounded corrective follow-up
- Telegram result delivery
- control repository immutability
- no Git delivery authority

CI currently exercises the repository across:

- Ubuntu / Python 3.9
- Ubuntu / Python 3.13
- macOS / Python 3.9
- macOS / Python 3.13

---

## Still pending live validation

The following have not yet been proven together in one real external DI-REMOTE-2 mission:

- live Telegram DI-REMOTE-2 routing
- live Mission Authorization approval
- real GitHub target clone
- real baseline materialization
- real target Herdr child bootstrap
- real Supervisor → Lead → Executor ↔ Reviewer target mission
- production fresh Codex role turns across the full Runtime path
- production evidence verification
- verified result returned to Telegram
- full control-repository immutability during the real mission

This distinction is intentional.

The repository does not claim live validation that has not happened yet.

---

# Always-on Mac

The trusted Mac remains the engineering node.

The system currently supports optional per-user LaunchAgents for the Telegram adapter and Runtime.

The Mac must retain:

- power
- internet connectivity
- Codex authentication
- Git authentication
- Herdr runtime access
- protected configuration
- local execution capability

No cloud Herdr service is required.

---

# Main commands

```text
herdctl init
herdctl upgrade
herdctl doctor
herdctl health
herdctl observe
herdctl status

herdctl mission create
herdctl mission show

herdctl task
herdctl task --mission
herdctl task-status
herdctl task-complete
herdctl task-abort

herdctl rules
herdctl review-decision

herdctl approve-commit
herdctl approve-push
herdctl push-tag

herdctl heartbeat
herdctl restart-heartbeat
```

Gateway:

```text
codexgw
```

Telegram:

```text
tgop
```

Remote workflow Runtime:

```text
dirun
```

---

# Roadmap

## Current milestone: live DI-REMOTE-2 validation

The next acceptance test is a real remote external-repository mission initiated from Telegram without manually operating the target repository.

Target experience:

```text
Phone
  |
Telegram
  |
Dodging Infinity control repository
  |
Mission Authorization
  |
Runtime
  |
managed external repository
  |
target Herdr
  |
verified result
  |
Telegram
```

No manual target clone.

No configuration switch.

No target terminal.

No manual Herdr registration.

No Git delivery without separate human authorization.

---

## Always-on Mac reliability

After the live cross-repository path is proven:

- reboot / login startup
- long-runtime reliability
- network-loss recovery
- service readiness
- protected state durability
- actionable host health

---

## Distribution / productization

The eventual user experience should become:

```text
Install Dodging Infinity
        |
        v
Connect models + GitHub + Telegram
        |
        v
Host readiness check
        |
        v
Ready
```

A user should not need to manually assemble:

- Python packages
- shell wrappers
- safety hooks
- Codex integration
- Herdr
- Telegram services
- Runtime services

---

## Desktop application

A desktop application may eventually provide:

- setup
- health
- configuration
- local status
- credentials / reauthentication
- service management

It must remain a client of the same authority model.

It must not become an alternate execution path around Codex or Herdr.

---

# Design constraint

Everything in Dodging Infinity remains subordinate to one rule:

> **Codex operates. Herdr engineers. Humans authorize delivery.**

Remote interfaces must not bypass Codex.

Runtime must not become an engineering planner.

Broker must not become an arbitrary command service.

Observability must not become control.

Distribution must not weaken authority boundaries.

The interface gets simpler.

The system underneath stays rigorous.

---

# License

Dodging Infinity is licensed under the [Apache License 2.0](LICENSE).