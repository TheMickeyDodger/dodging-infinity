# Dodging Infinity roadmap

Status: Working roadmap\
Created: August 27, 2026\
Updated: September 4, 2026

This is the single roadmap for Dodging Infinity. It carries the long arc
(Phase 0 to Phase 11), the detailed near-term sequence (Phase I to Phase V,
Iterations 0 to 18), and the long-term milestones. Release history lives in
[CHANGELOG.md](../CHANGELOG.md); the evidence behind v0.7.0 is on the
[release evidence](release-evidence-v0.7.0.md) page; the system being built is
described in [architecture.md](architecture.md).

Two numbering systems appear below and they describe the same work from
different distances. The Phase 0 to Phase 11 arc says what each stage builds
and what it must prove. The Phase I to Phase V iterations are the detailed
near-term plan with per-iteration acceptance statements, and they are
test-pinned. Neither supersedes the other: Phases 0 to 2 correspond roughly to
Iterations 0 to 3 and the release gate, and the milestones map onto Phases 3 to
11. Where they disagree on the fine grain, the iterations are the nearer view.

Progress notation: ~~crossed out~~ = completed, or proven by the historical
external-target mountain before it terminated BLOCKED; uncrossed = still open.

## North Star

> I can leave the trusted Mac unattended for seven days, initiate
> multiple independent missions from anywhere, talk naturally about any
> one of them, observe all of them instantly, receive reviewed
> results and artifacts, authorize exact reviewed delivery actions from
> my phone, and recover any individual service failure without being
> physically present.

Dodging Infinity should evolve from one Codex thread reachable through
Telegram into a persistent remote mission fabric.

``` text
Human
  -> Telegram
  -> Telegram Adapter
  -> Mission Router
       -> Mission A -> Mission Codex -> Herdr
       -> Mission B -> Mission Codex -> Herdr
       -> Mission C -> Mission Codex -> Herdr
       -> New Mission
  -> Durable Mission State
       -> immediate status
       -> results
       -> artifacts
       -> delivery approvals / receipts
```

Separately:

``` text
Phone / Laptop -> Tailscale / SSH -> Trusted Mac
```

That is a break-glass recovery path, not normal mission authority.

## Controlling principles

-   Human owns intent and material delivery authority. Telegram may
    transport an exact human authorization, but it never mints delivery
    authority by itself.
-   Mission Router routes conversation identity only. It does not
    engineer.
-   Codex operates: intent, mission bounds, lifecycle decisions,
    independent verification.
-   Herdr engineers: Supervisor is the first strategy-bearing component.
-   Runtime remains deterministic and does not invent work.
-   Observability is read-only unless a separate bounded action is
    explicitly authorized.
-   Prefer truthful BLOCKED, STALE, AMBIGUOUS, or NEEDS HUMAN states
    over guessing or replaying uncertain effects.
-   One mission must never inherit another mission's authority, state,
    artifacts, approvals, or context.

# The long arc: Phase 0 to Phase 11

## Phase 0: v0.7.0, complete

The v0.7.0 release and its reference proof. DI-REMOTE-2 acceptance is complete
for the release tree: reconciled public documentation, hermetic clean-clone CI
(run `33330263889`), the historical mountain preserved as terminal diagnostic
evidence, the Runtime stabilization lineage integrated into `main`, final
certification on the stable tree (continuation task `20260901-165812-045b0c`,
2,048 tests, `OK (skipped=1)`), and the release tree proven green. Tag
publication is governed separately by the human authorization gate.

v0.7.0 is the reference proof for remote mission authorization, remote target
materialization, Herdr bootstrap, independent verification, exactly-once result
delivery, and human Git gates. The evidence trail is on the
[release evidence](release-evidence-v0.7.0.md) page.

Completed foundations, accumulated through v0.6.1, v0.6.2, v0.6.3, and v0.7.0:
the durable Herdr mission boundary; Supervisor to Lead to Executor and Reviewer
orchestration; repository isolation; the deterministic review protocol; human
commit, push, and tag gates; the Codex Operator contract; the plan-scoped
operator protocol; `herdctl health` and `herdctl observe` with a
schema-versioned observation model; Codex Gateway v0.1 with live compatibility
validation; the Telegram Remote Operator MVP with a numeric allowlist,
private-chat enforcement, one-shot fully bound plan approval, resumed sessions,
status, and verified-result delivery; the optional per-user LaunchAgent; and
DI-REMOTE-2 Remote Target Repository Routing.

## Phase 1: host survival and seams

In progress. Host survival and architectural seams, with v0.7.0 behavior
preserved. The list: Tailscale and SSH break-glass recovery; reboot and login
survival; service identity and readiness; `HumanInteractionAdapter`;
`OperatorSession`; `DurableExecution`; `Capability`; `Worker`; a DBOS spike; a
Pi spike; a Grok Bot spike. Telegram and Codex stay reference implementations
throughout.

What exists in this checkout are the initial `OperatorSession`
`prepare()` / `execute()` seam, the `HumanInteractionAdapter` seam with
`TelegramHumanInteractionAdapter` behind it, and the first `DurableExecution`
and `Capability` seams. All are initial abstractions rather than the target
lifecycles, and the rest of the list is open. See
[architecture.md](architecture.md#16-current-implementation-notes).

## Phases 2 to 5

| Phase | Builds | Proves |
|---|---|---|
| 2: Mission Harness | Mission Manifest, Mission Registry, `M-####` identity, PREFLIGHT, the Mission Authorization gate, AWAITING_MISSION_AUTHORIZATION and NEEDS_REAUTHORIZATION, the lifecycle state machine, Authority Ledger, Evidence Graph, Blocker Ledger, proof requirements, Artifact Registry, budgets, continuation and checkpoints, event journal, snapshots, readiness graph, Reconciler, Observation Service. | The actual mission operating system exists and canonical state is the truth. |
| 3: Routing, attention, and Grok | The natural-language Mission Router with deterministic ambiguity handling, the Attention Router, instant read-only status queries, the Grok interaction adapter with authorization and result cards and exact approval transport, the Telegram fallback adapter, shared-computer security tests, recovery and parity tests. | Grok becomes the preferred front door only once proven; Telegram remains the fallback. |
| 4: Provider-neutral Operator | The full `OperatorSession` lifecycle, the Codex adapter, the Pi RPC adapter, bounded DI tools for Pi, provider and model selection, Domain Operator Profiles, Skill Packs, generated capability documentation, cross-model evaluations. | Provider replacement does not change mission authority or semantics. |
| 5: True multi-mission execution | Independent execution lanes, bounded admission, the Scheduler, per-mission queues, workspace, artifact, and approval isolation, fair scheduling, P0 to P3 priority, independent Herdr Pods, mission relationships, pause, resume, and abort, expensive-verification deduplication. | Three simultaneous missions with zero cross-contamination. |

## Phases 6 to 9

| Phase | Builds | Proves |
|---|---|---|
| 6: Evidence-native capabilities and delivery | BrowserCapability with read and write classification, stale-reference failure, screenshot and snapshot evidence, console and network evidence, ambiguous side-effect reconciliation, human browser handoff; Artifact Registry delivery and richer file types; the Action Risk Envelope; proof-complete feedback loops; the exact remote delivery ceremony in Grok. | Real user-path evidence and exact remote delivery are first-class. |
| 7: Chaos and worker fabric | Injected failure of Grok, Telegram, the Runtime, the Operator, Herdr, sleep and wake, reboot, network, GitHub, model, quota, stale process, blocked mission, ambiguous browser action, interrupted result, artifact, and Git or release action, missed events; then the Worker Registry, leases, simulator, VPN, and GPU capability matching, retention, archival, compaction. | Multiple missions survive a hostile day without losing identity, authority, observability, progress, results, artifacts, or recovery information. |
| 8: Organizational learning | The Ops Steward, repeated-failure and same-mistake-twice detection, repetition-to-automation, bounded nightly missions, recurring monitoring, the postmortem flow, Skill and Profile proposal flow, control metrics, cost reporting. | Recurring interventions become deterministic machinery through a governed path; the Steward cannot change its own authority. |
| 9: Productization | A deterministic installer and upgrader, migrations, rollback, generalized target onboarding, a safe "go solve this issue" flow, an operational desktop, worker and environment onboarding. | Install once, connect a transport, point at a repository, start engineering, without hand-assembling infrastructure. |

The distribution end state is part of Phase 9: an installer that verifies the
host, installs and verifies the Herdr runtime, `herdctl`, `codexgw`, the
Operator integration, operator contracts, safety guards, the Telegram adapter,
and the Mac background service, then runs a deterministic health check and
confirms readiness; repository onboarding reduced to `herdctl init`; a desktop
app later, as a client of the same operator boundaries and never a replacement
execution path.

## Phases 10 and 11

Phase 10 is the general autonomous work fabric: engineering, research,
automation assessment, report generation, analysis, monitoring, planning,
browser QA, release preparation, and maintenance formalized as native mission
classes sharing identity, routing, observability, evidence, and authority
infrastructure, plus end-to-end operations transformation missions.

Phase 11 is the Visual Mission OS. It is last. It is an immersive projection of
canonical state over the Harness's APIs and events (a snapshot on startup, a
subscription after the last event sequence, catch-up or a fresh snapshot when
events were missed), and it never spawns Herdr, approves missions, marks
missions complete, creates Git authority, deploys, or rewrites durable state.
Acceptance requires every lower layer to be stable first. Munder Difflin is the
presentation design reference for it: its ideas about showing a multi-agent
organization are reused; its orchestration and authority backend are not.

### Why the visual world is last

A visual world is only worth building over state that is already canonical,
durable, observable, and recoverable. Built earlier, it would either invent
state the backend does not hold or become a second control path around the
authority model, which is the one thing the design forbids. Built last, it is a
consequence of real events: Supervisor planning, Executor working, Reviewer
rejected, mission blocked, verification running, artifact registered, human
approval ready, delivery locked. If it crashes, the missions keep running. The
renderer is never the backend.

# Where the system is today

## Foundation proven by the historical external-target mountain

The historical external-target mountain is TERMINAL. It exposed genuine
post-dispatch policy drift and correctly terminated BLOCKED at
`broker_verification_policy_drift`.
Its identifiers, for the record: workflow `wf-2c901885473fc4781bf82296`,
target Herdr task `20260830-094026-9fef2d`, target baseline
`3e1833d930723ef4f7220698c98155a925591d4d`, from a natural-language request
targeting an external repository issue.

Before it terminated, that mountain did prove the following DI-REMOTE-2
foundation outside tests:

-   ~~A natural-language Telegram request can trigger a fresh restrictive
    Codex planning turn and render a bounded Mission Authorization.~~
-   ~~A Telegram button approval can durably authorize exactly that mission
    while typed text carries no authority.~~
-   ~~Runtime can claim the authorization from the durable workflow store
    without dispatching another Gateway turn.~~
-   ~~Runtime can materialize an isolated target workspace at the exact
    authorized baseline with no manual clone, registration, terminal, or
    target Herdr setup.~~
-   ~~Target instructions can be collected during preparation and the exact
    bounded handoff can pass a fresh restrictive handoff-validation turn.~~
-   ~~Broker/Runtime can dispatch the byte-exact handoff while preserving
    `delivery_authority: none`.~~
-   ~~The target Herdr can bootstrap unattended with Supervisor, Lead,
    Executor, and Reviewer all registered and interactive-ready.~~
-   ~~Supervisor remains the first strategy-bearing component and can hand
    execution to Lead/Executor while Reviewer waits adversarially.~~
-   ~~Durable `/status` can report a live DI-REMOTE-2 workflow phase and
    target task while the mission continues independently.~~
-   ~~The live target preserves human Git gates (`commit: require-human`,
    `push: require-human`) and cannot deliver from Mission Authorization.~~
-   ~~Owned role-turn spawning preserves PATH lookup and process/session
    ownership, with the full committed regression suite green.~~
-   ~~The target Herdr task reached COMPLETE and a canonical target Reviewer
    APPROVE was recorded.~~
-   ~~Target observation refreshed from a stale `ACTIVE` reading to
    `COMPLETE`.~~

Terminal outcome. The workflow then stopped BLOCKED at
`broker_verification_policy_drift`. `verified_result` and
`result_delivery` remained null. No target Git delivery occurred: the
target stayed at baseline `3e1833d930723ef4f7220698c98155a925591d4d`
carrying an implementation diff only. Everything downstream of the drift stop did not run in that historical
execution. The defects it exposed were subsequently closed, the Runtime
stabilization lineage was integrated into `main`, and the corrected
verification plus exactly-once final-result path was certified hermetically
and adversarially for v0.7.0. A fresh post-fix live mountain is not used as
release evidence. Separate artifact delivery remains outside that
certification. DI-REMOTE-2 acceptance is COMPLETE for the v0.7.0 release
candidate.

## Immediate release gate: DI-REMOTE-2 acceptance before Phase I

The remote mission fabric does not begin from an unaccepted moving target.
The release sequence is now:

1.  [x] Complete the README and documentation reconciliation.
2.  [x] Repair clean-clone CI hermeticity: runner-equivalent local validation
    passed, and all four PR matrix jobs (macOS and Ubuntu x Python 3.9 and
    3.13) are green in CI run `33330263889` at
    `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f`; the branch also passed at
    `52a97b71a3b5c9f20ff33d4feb1332284cd825b7`.
3.  [x] Preserve the historical external-target mountain as terminal diagnostic
    evidence: it reached target Herdr COMPLETE and then correctly stopped
    BLOCKED at `broker_verification_policy_drift`.
4.  [x] Integrate the reviewed and pushed Runtime stabilization commit
    `d8ec2af409e4086f985be03371a872a84a3767ec` from branch
    `fix/runtime-terminal-reconciliation` into `main`.
5.  [x] Complete final DI-REMOTE-2 certification on the stable tree:
    continuation task `20260901-165812-045b0c` reached COMPLETE, Reviewer
    persisted APPROVE, and the authoritative discovery ran 2,048 tests with
    `OK (skipped=1)` and exit 0.
6.  [x] Prepare the v0.7.0 release tree with reconciled public docs and
    preserved historical evidence.
7.  [x] Prove the exact v0.7.0 release tree green in CI; tag publication is
    governed separately by the human authorization gate.

Historical stabilization evidence remains part of the release record:
task `20260830-185309-4c3db7`, final canonical Reviewer round 6 APPROVE,
focused regression 159/159, `tests/test_target_runtime.py` 250/250, static
checks PASS, Python 3.9.6 compile PASS, and `git diff --check` PASS. The
historical repository-wide LIVE working-tree loop stood at 35/37 solely
because pre-existing live `.herd` specimen assertions in
`tests/test_hermetic_git.py` and `tests/test_reconcile_audit.py` predate
that task.

The historical external-target mountain remains truthful historical evidence:
it terminated BLOCKED before final verification/result delivery. The corrected
final-result contract is certified by the later hermetic/adversarial release
evidence; a fresh post-fix live mountain is not used as release evidence.
Separate artifact delivery is not claimed by that certification.

Acceptance:

> DI-REMOTE-2 acceptance is complete when the public repository, exact release
> commit CI, canonical review evidence, and authoritative unchanged-tree test
> run all describe the same bounded system.

# Phase I: Remote Mission Fabric

## Iteration 0: Trusted Mac stabilization and break-glass access

Reconcile the host before more feature work.

Work:

-   ~~synchronize `main` and `origin/main` at
    `cda06d8c502882672667d94821b8bd00e7060a52`~~
-   ~~migrate Telegram durable state to the current schema~~
-   ~~reload current tgop and dirun after code changes and verify fresh
    running processes~~
-   ~~verify dirun, target Herdr bootstrap, Codex execution, Git human
    gates, launchd, config, and durable workflow state through the active
    portion of the live DI-REMOTE-2 mountain~~
-   ~~integrate the pushed Runtime stabilization commit
    `d8ec2af409e4086f985be03371a872a84a3767ec` and assemble a stable `main`~~
-   ~~certify independent verification, VERIFIED/COMPLETED, and exactly-once
    final-result delivery hermetically and adversarially on the stable tree~~
-   configure Tailscale plus SSH, restricted to trusted devices/accounts
-   avoid public inbound SSH exposure
-   verify persistence across reboot/login

Current state: the Runtime stabilization lineage is integrated, DI-REMOTE-2
acceptance is complete for the v0.7.0 release tree, and the corrected
final-result contract is certified on the stable tree. A fresh post-fix live
mountain is not used as release evidence. Remaining Iteration-0 work is the
break-glass Tailscale/SSH and reboot/login persistence work; release tagging
is governed separately by its human authorization gate.

Test from a genuinely remote network with Telegram healthy, Telegram
stopped, Runtime stopped, Codex wedged, Herdr wedged, and a stale
LaunchAgent.

Acceptance:

> If Telegram completely dies while I am away, I can still securely
> reach the trusted Mac and recover it.

## Iteration 1: Durable Mission Registry and Mission Router

Create a first-class layer above mission-specific Codex sessions.

Each mission receives a durable ID such as M-0042 plus: - title -
original intent - mission type - status - source chat - Codex session -
Herdr task - workflow ID - target - aliases - timestamps - result
state - artifact state

Routing order should prefer deterministic evidence: 1. explicit mission
ID 2. Telegram reply-to binding 3. approval/result binding 4. exact repo
or issue reference 5. known company/project 6. durable aliases 7. unique
contextual match 8. bounded fresh routing-model turn

Allowed model outcomes:

``` text
existing_mission: M-0042
new_mission
clarification_required
```

If "Why is this taking so long?" could refer to several missions, ask
which one rather than guessing.

Progress: durable `wf-*` workflow identity, Telegram binding, target identity,
and task identity now exist and survive independently of the Gateway turn. The
first-class `M-####` registry and natural-language Mission Router remain open.

Acceptance: the historical external target, Silvi, and another mission can all be active and
natural-language follow-ups reliably reach the right mission.

## Iteration 2: Per-mission asynchronous execution lanes

Remove long-running missions from Telegram's single serialized worker.

``` text
Telegram inbound
  -> Mission Router
       -> M-0041 queue -> Codex / Runtime / Herdr
       -> M-0042 queue -> Codex / Runtime / Herdr
       -> M-0043 queue -> Codex / Runtime / Herdr
```

~~After durable authorization/dispatch, the Telegram request ends and the
mission continues independently under Runtime/Herdr.~~

The remaining work is true per-mission concurrency and queue isolation rather
than merely decoupling one long-running mission from the inbound Telegram turn.

Add bounded concurrency: - maximum active missions - maximum
simultaneous Herdr tasks - explicit capacity behavior - fair
scheduling - isolation between queues, approvals, contexts, state, and
artifacts

Acceptance: start Silvi, then an external-target mission, then a third mission, and continue
interacting with all three while Telegram remains responsive.

## Iteration 3: Out-of-band observability and mission control

Core commands:

``` text
/missions
/status
/status M-0042
```

Global status should show adapter health/version, disk vs running
commit, schema, Runtime health, active model turns, active/queued
missions, Herdr tasks, blocked/stale missions, and result/artifact
delivery backlog.

Mission status should show lifecycle phase, elapsed time, current Codex
activity, Herdr task, agent states, latest durable progress, review
round, result state, artifact state, and health classification.

Hard requirement: status reads durable state and bounded read-only
observability directly. It never waits for the mission-specific Codex
turn.

~~Live proof: `/status` read the durable v2 workflow store while the
historical external-target mission WAS ACTIVE and reported Runtime state, workflow phase,
target, and target Herdr task without waiting for the mission Codex turn.~~ The richer
mission-control surface above remains open.

Acceptance:

> /status responds within roughly five seconds while an eight-hour
> mission is running.

## Iteration 4: Runtime identity, upgrade readiness, and safe service lifecycle

Prevent a repeat of the stale v1 Telegram process.

Every long-running service should expose:

``` text
service
pid
started_at
running_version
running_commit
disk_version
disk_commit
state_schema
required_schema
config_path
health
```

Detect new code on disk, schema mismatch, missing migration, outdated
LaunchAgent, missing/moved executables, and detectable auth problems.

Add fixed operational actions such as health, restart Telegram operator,
restart Runtime, and reload after upgrade. These are not arbitrary
shell. They must inspect in-flight state and refuse unsafe restart.

Acceptance:

> New code on disk cannot leave an apparently healthy obsolete daemon
> running silently.

Progress: ~~Telegram Operator and Runtime can be deliberately reloaded onto a
new committed control-plane increment and verified with fresh singleton PIDs.~~
Automatic running-commit/disk-commit skew detection and safe in-flight restart
policy remain open.

## Iteration 5: Telegram exact delivery authority and Git decision surfaces

Make the phone capable of completing the delivery ceremony without weakening
the human gate. This is now a core requirement, not an optional someday
feature.

The phone-facing ceremony is explicit and ordered:

``` text
Verified result
  -> Inspect exact diff / evidence
  -> Prepare commit
  -> Approve commit
  -> Commit receipt
  -> Approve push OR Open PR
  -> Push / PR receipt
  -> Optional later: Approve merge / tag / release / deploy
```

Mission Authorization grants **ZERO delivery authority**. Delivery begins only
after the mission is complete, Reviewer-approved, and independently verified.
Each delivery action is an independent one-shot capability; no action inherits
authority from Mission Authorization or from another delivery action.

The delivery model must use closed, one-shot capabilities:

-   `Prepare commit` is read-only: compute and render the exact target repo,
    mission/workflow ID, baseline, current HEAD, diff summary, changed paths,
    staged-tree/diff digest, validation evidence, Reviewer decision, and
    proposed commit message.
-   `Approve commit` binds the exact repository, mission/result revision,
    HEAD, exact staged bytes/digest, commit message, human/chat, expiry, and a
    one-shot nonce. Any byte, HEAD, mission revision, or policy change
    invalidates it. Typed Telegram text cannot authorize it.
-   Commit execution is deterministic and uses the existing Herdr/Git commit
    gate; no `--no-verify`, no arbitrary shell, and no authority reuse.
-   `Approve push` is a **separate** one-shot capability bound to the exact
    resulting commit SHA, remote/ref, expected remote state, human/chat,
    expiry, and nonce. Commit approval never implies push approval.
-   `Open PR` / PR update is another closed action bound to the exact source
    commit, destination, title/body digest, and current remote state.
-   `Approve merge` is separate from PR creation and binds the exact PR, head
    SHA, base, merge method, required checks/reviews state, human/chat, expiry,
    and nonce.
-   `Approve tag`, `Approve release`, and any future `Approve deploy` each
    require their own capability. Release binds the exact tag/commit and
    release body/artifact digests. Deploy binds the exact immutable revision
    and environment.
-   No delivery action inherits authority from Mission Authorization, commit,
    push, PR creation, or any other delivery action or mission.
-   Every delivery attempt writes a durable receipt with `prepared`,
    `authorized`, `executing`, `succeeded`, `failed`, or `ambiguous` state and
    reconciles uncertain external effects before allowing another attempt.
-   `/status` must surface pending delivery decisions, exact bound commit/ref,
    expiry, and any ambiguous or blocked delivery state.

Acceptance:

> I can receive a verified engineering result in Telegram, inspect the exact
> diff/commit proposal, approve one local commit from the phone, then separately
> approve its exact push or PR without touching the Mac, while a stale or
> replayed button can never authorize a different result.

## Iteration 6: First-class artifact delivery

Start with reviewed Markdown artifacts.

Artifacts must: - belong to one mission - live in an approved artifact
location - be registered in durable state - have an exact digest - have
allowed type and bounded size - pass containment checks - reject
symlinks/devices/FIFOs/unrelated files - have explicit delivery state

Delivery states should distinguish pending, reserved, partial,
delivered, ambiguous, and failed.

Later add PDF, CSV, XLSX, DOCX, PPTX, and images deliberately rather
than allowing arbitrary files.

Acceptance:

> Request the Silvi mission from the phone and receive the
> Reviewer-approved Markdown artifact without touching the Mac.

## Iteration 7: Multi-mission mountain and chaos test

Run at least three concurrent missions: - external-target engineering -
Silvi operational-automation research - one unrelated third mission

Inject failures: - kill Telegram adapter - kill Runtime - kill a Herdr
process - sleep/wake Mac - disconnect/reconnect network - reboot Mac -
temporarily lose Codex access - hit model quota - leave one mission
blocked - create stale runtime after update - interrupt result/artifact
delivery

One mission failure must not take down another. Uncertain external
effects must reconcile deterministically, block durably, or require
explicit human recovery.

Acceptance:

> Operate multiple missions remotely for a full day under hostile
> conditions without losing identity, authority, observability,
> progress, results, artifacts, or recovery information.

# Phase II: Always-On Trusted Host

## Iteration 8: Reboot, login, sleep/wake, and network resilience

Validate cold reboot, login startup, service ordering, sleep/wake, long
sleep, Wi-Fi loss/recovery, router restart, DNS failure, and temporary
GitHub/Telegram/model outages.

Acceptance: ordinary host and network lifecycle events do not require
physical intervention.

## Iteration 9: Host readiness and dependency graph

Create one deterministic readiness model covering Telegram, Mission
Router, Runtime, Codex, Herdr, GitHub, Git credentials, artifact
delivery, and target child bootstrap.

Expose READY, DEGRADED, BLOCKED, and actionable reasons.

~~Target-child bootstrap readiness is now a durable production receipt: the
historical external-target mountain recorded all four logical roles registered and
interactive-ready before engineering proceeded.~~ The broader host dependency
graph remains open.

Fail before consequential dispatch when a required dependency is known
unavailable.

Acceptance:

> Before accepting consequential work, Dodging Infinity can tell whether
> the host can actually execute it.

## Iteration 10: Durable-state hygiene and long-run maintenance

Define bounded retention, archival, compaction, and cleanup for
completed missions, Codex metadata, Herdr history, reviews, workflow
records, Telegram bindings, artifacts, logs, stale approvals, and
migration backups.

Cleanup must never destroy active authority or audit evidence.

Acceptance: months of operation without manual state-directory
housekeeping or unbounded growth.

# Phase III: Mission Fabric Maturity

## Iteration 11: Mission priority, capacity, and scheduling

Add explicit resource management: - mission priority - queued vs
active - bounded concurrent Herdr work - model quota awareness - fair
scheduling - pause/resume where semantics permit - urgent capacity
reservation - starvation prevention

Router owns identity. Scheduler owns capacity. Do not combine them.

## Iteration 12: Mission relationships and compound work

Support explicit parent/child, dependency, follow-up, and supersedes
relationships.

Example:

``` text
Parent: Research Silvi
Child: Build pilot architecture
```

Related missions never automatically inherit each other's authority.

## Iteration 13: Rich remote decision surfaces

Evolve Telegram into a clear mission console with bounded controls for:

-   mission selection
-   status
-   artifacts
-   blocked-condition acknowledgement
-   permitted recovery
-   mission approval/rejection
-   exact diff inspection
-   `Prepare commit`
-   `Approve commit`
-   `Approve push`
-   `Open PR` / PR update
-   `Approve merge`
-   tag/release/deploy approval when enabled
-   authorization history
-   expiry/replay state
-   exact-result receipts

Every control remains bound to exact mission, revision, human, chat, and
durable state.

# Phase IV: Distribution and Productization

## Iteration 14: Deterministic installer and upgrader

Target:

``` text
Install Dodging Infinity
  -> verify host
  -> install/verify Herdr, herdctl, Codex integration, Telegram, Runtime, Router, guards
  -> configure credentials
  -> run readiness check
  -> READY
```

The upgrader must understand runtime/disk skew, migrations, service
reload, and rollback/recovery.

## Iteration 15: Repository and target onboarding

~~For the historical external-target path, remote target setup now requires no manual
clone, registration, terminal, or Herdr setup: Runtime materialized the pinned
workspace, collected target instructions, bootstrapped Herdr, and dispatched the
mission from one Telegram authorization.~~

Generalize that proof across supported repositories and complete remaining
target hardening exposed by live DI-REMOTE-2 testing, including path-scoped
target instruction handling and repository-specific compatibility edges.

Acceptance:

> "Go solve this issue" is enough to establish a safe isolated target
> mission when the target is supported.

## Iteration 16: Desktop control application

Build a local client for setup, health, missions, configuration,
credentials, service management, logs, and recovery.

It remains a client of the same authority model and never becomes an
alternate execution path around Codex, Runtime, Broker, or Herdr.

# Phase V: Broader Autonomous Work Platform

## Iteration 17: Generalized mission types

Formalize native mission classes: - engineering - research - operational
automation assessment - document/report generation - analysis -
monitoring - planning

Different mission types can have different artifact expectations and
Herdr guidance while sharing identity, routing, observability, evidence,
and authority infrastructure.

## Iteration 18: AI operations transformation missions

Formalize the Infinity/Ocean Block style of work.

Given a company or industry, Dodging Infinity should: - investigate
manual processes - find humans acting as middleware between systems -
reconstruct current-state workflows - identify reconciliation, inbox,
spreadsheet, document, approval, and exception work - design AI
orchestration that sits across existing systems of record - identify
what human work disappears - estimate ROI mechanisms - design pilots -
adversarially review the analysis - produce implementation-ready
artifacts

Acceptance:

> A company prompt can become a Reviewer-approved automation opportunity
> map and pilot architecture.

# Long-Term Milestones

## Milestone A: Remote survivability

The Mac is recoverable from anywhere even when Telegram is broken.

## Milestone B: DI-REMOTE-2 released

The v0.7.0 release tree has completed DI-REMOTE-2 acceptance and exact
release-tree CI is green. Tag publication is governed separately by the human
authorization gate. The v0.7.0 release tree is the baseline for Mission Router
and concurrency work.

## Milestone C: Multi-mission operation

Telegram can naturally control multiple concurrent missions without
context collision.

## Milestone D: Immediate observability

Status is always available independently of active work.

## Milestone E: Full remote delivery authority

A verified result can move through exact, one-shot, human-approved commit and
separately authorized push/PR actions from Telegram without granting ambient or
replayable delivery authority.

## Milestone F: Artifact-native work

Research and engineering missions return durable reviewed files, not
only chat text.

## Milestone G: Unattended reliability

The trusted Mac survives ordinary host/network events and exposes
readiness before work starts.

## Milestone H: Productization

Install, configure, upgrade, diagnose, and operate Dodging Infinity
without manually assembling its infrastructure.

## Milestone I: General autonomous work fabric

Engineering, research, operational automation analysis, and other
bounded mission types all operate through the same durable mission
architecture.

# Final Product Principle

> Codex operates. Herdr engineers. Humans authorize consequential
> boundaries from wherever they are. The transport never weakens those
> boundaries. The Mission Router keeps the mission fabric coherent.

The interface should become simpler while the authority model underneath
remains rigorous.
