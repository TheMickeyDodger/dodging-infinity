# Dodging Infinity: Remote Mission Fabric Roadmap

Status: Working roadmap\
Created: August 27, 2026\
Updated: August 30, 2026

Progress notation: ~~crossed out~~ = live-proven or completed; uncrossed = still open.

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

## Live-proven foundation as of August 30, 2026

The following DI-REMOTE-2 foundation is now proven on the live Mitiq
#2802 mountain rather than only in tests:

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

Still being proven by the current mountain: Reviewer completion, final
independent Codex verification, deterministic VERIFIED/COMPLETED transition,
and exactly-once Telegram result delivery.

## Immediate release gate: DI-REMOTE-2 acceptance before Phase I

The remote mission fabric does not begin from an unaccepted moving target.
The required product sequence is:

1.  [x] Publish the README and documentation update in this PR.
2.  [ ] Complete the live Mitiq #2802 mountain, including target Reviewer,
    independent verification, VERIFIED/COMPLETED, and exactly-once Telegram
    result evidence.
3.  [ ] Repair clean-clone CI hermeticity and confirm both runner-equivalent
    local validation and all four PR matrix jobs.
4.  [ ] Perform final DI-REMOTE-2 acceptance against the combined public,
    automated, and live evidence.
5.  [ ] Create the DI-REMOTE-2 release/tag only when the live mountain and
    clean-clone CI agree.
6.  [ ] Only then begin the Durable Mission Registry and Mission Router.

The current Mitiq mountain is not complete: its final Reviewer,
verification, lifecycle, and result-delivery stages remain open until durable
evidence proves them. DI-REMOTE-2 also remains unreleased; no tag is implied
by implementation or by partial live proof.

Acceptance:

> DI-REMOTE-2 is released only when the public repository, clean-clone CI,
> and live Telegram-to-target mountain all describe and prove the same
> system.

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
-   complete the final Mitiq Reviewer, independent-verification,
    VERIFIED/COMPLETED, and exactly-once result-delivery stages
-   configure Tailscale plus SSH, restricted to trusted devices/accounts
-   avoid public inbound SSH exposure
-   verify persistence across reboot/login

Current state: `main` and `origin/main` agree at the SHA above. The current PR
branch carries the public documentation and CI-hermeticity work without
changing `main`. Remaining gaps are final Mitiq lifecycle acceptance,
unconfigured break-glass Tailscale/SSH, and the still-unreached DI-REMOTE-2
tagged release.

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

Acceptance: Mitiq, Silvi, and another mission can all be active and
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

Acceptance: start Silvi, then Mitiq, then a third mission, and continue
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

~~Live proof: `/status` already reads the durable v2 workflow store while the
Mitiq mission is ACTIVE and reports Runtime state, workflow phase, target, and
target Herdr task without waiting for the mission Codex turn.~~ The richer
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

Run at least three concurrent missions: - Mitiq #2802 engineering -
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
Mitiq mountain recorded all four logical roles registered and
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

~~For the live Mitiq #2802 path, remote target setup now requires no manual
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

Unreached. Live mountain evidence, clean-clone green CI, public docs, and the
tagged release all agree. That tagged release is the baseline for Mission
Router and concurrency work.

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
