# Architecture

This is the full system. The [README](../README.md) is the front door and
deliberately shows a simplified version; this page describes the whole design,
marks where this checkout sits inside it, and keeps the vocabulary in one
place. Running the system is [operations.md](operations.md). What is built
next, and in what order, is the [roadmap](roadmap.md).

Dodging Infinity is a governed mission fabric for giving AI real work without
giving AI uncontrolled authority. The operating rule:

> **Bots converse and collaborate. Dodging Infinity governs. Capabilities do
> bounded work. Workers execute. Humans authorize.**

It is not a wrapper around Telegram, Grok, Codex, or Pi. It is not a persona
collection, a multi-agent chat demo, a task board, a generic workflow engine,
or an autonomous Git bot. It is not merely Herdr, and it is not a Herdr UI. It
is not a visual simulation that controls agents. Each of those can be an
interface, an implementation, a dependency, a candidate, or a reference. The
product is the governed mission fabric that joins them.

## How to read this document

Most of what follows is design. Section 16 is the single status table: one row
per subsystem, saying what exists in this checkout, what is an initial seam,
and what has no machinery in the tree at all. Rather than labelling every
paragraph, this page states the target and then names the narrower thing that
exists today wherever the two differ. When this page and a test disagree, the
test is right and this page is stale.

## 1. North Star

```text
You
 │
 │ text / voice / image / file / video / link
 ▼
┌──────────────────────────────┐
│      GROK BOT COORDINATOR    │
│  your universal front door   │
└──────────────┬───────────────┘
               │
               │ conversations, mission requests,
               │ approvals, status questions
               ▼
┌─────────────────────────────────────────────────────────────┐
│                DODGING INFINITY ORCHESTRATOR                │
│                                                             │
│  Creates Missions                                           │
│  Owns Mission truth                                         │
│  Owns authorization                                         │
│  Routes work                                                │
│  Watches work                                               │
│  Reconciles reality                                         │
│  Verifies results                                           │
│  Enforces delivery gates                                    │
└──────────────┬───────────────────────────┬──────────────────┘
               │                           │
        engineering                   other missions
               │                           │
               ▼                           ▼
        ┌─────────────┐       ┌─────────────────────────────┐
        │    HERDR    │       │ Research / Browser / Ops /  │
        │ engineering │       │ Marketing / Media / future  │
        └──────┬──────┘       └─────────────┬───────────────┘
               │                            │
               └──────────────┬─────────────┘
                              ▼
                   ┌────────────────────┐
                   │      WORKERS       │
                   │ trusted Mac first  │
                   └────────────────────┘
```

The operating goal, stated as an acceptance test: leave the trusted Mac
unattended for seven days, start multiple independent Missions from anywhere,
talk naturally about any one of them, observe all of them instantly, receive
reviewed results and artifacts, authorize exact reviewed delivery actions from
a phone, and recover any individual service failure without being physically
present.

Each role in the fabric has a limit, and the limit is the point:

| Role | What it does | What it never does |
|---|---|---|
| Grok Bot | The conversational plane, with specialist experiences for coordination, research, operations, release, and QA. | Owns authority. |
| Operator | The replaceable reasoning role for one Mission. Assesses evidence, chooses bounded work, invokes approved capabilities, coordinates engineering when required. | Becomes a model brand. Mints authority. |
| Herdr | The engineering organization inside an engineering Mission: Supervisor, Lead, Executor, Reviewer. | Becomes the Mission control plane. |
| Dodging Infinity | The authoritative system: Mission identity, authorization, lifecycle, rules, evidence, blockers, artifacts, scheduling, budgets, workers, checkpoints, recovery, reconciliation, canonical status, delivery receipts. | Delegates authority to a model, a transport, or a UI. |
| Humans | The root of consequential authority. | Get replaced by an approval a machine minted. |
| Reconciler | Deterministic machinery that compares durable expected state with reality. | Acts as an AI agent. |
| Ops Steward | A later organizational learning layer that finds repeated failures and proposes improvements. | Expands its own authority. |
| The world | A visual projection of canonical state, deliberately last. | Becomes the backend. |

### The complete logical architecture

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                                  HUMAN                                       │
│ Phone / Desktop / CLI                                                        │
│ Text • Voice • Audio • Images • Screenshots • Video • PDFs • Files • URLs     │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            GROK BOT PLANE                                    │
│  Coordinator Grok Bot                                                        │
│      ├── Research Bot   ├── Engineering Bot   ├── Marketing Bot              │
│      ├── Ops Bot        ├── Finance Bot       └── Future Bots                │
│                                                                              │
│ Bots share mission refs, artifact refs, evidence, questions, summaries and    │
│ recommendations. They do NOT exchange hidden authority.                      │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         INTERACTION PLANE                                    │
│ HumanInteractionAdapter · Mission Query / Command API                        │
│ Multimodal Intake · Artifact Resolver                                        │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            ROUTING PLANE                                     │
│  Mission Router      What mission / command is this?                         │
│  Attention Router    What needs the human now?                               │
│  Bot Coordination    Which specialist bot should participate?                │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    DODGING INFINITY MISSION HARNESS                          │
│  Mission Registry / Manifest          Artifact Registry + Delivery Receipts   │
│  Mission Authorization + Ledger       Action Risk Envelope                    │
│  Lifecycle State Machine              Budget / Continuation / Closure Policy  │
│  Evidence Graph + Proof Requirements  Checkpoints + Recovery State            │
│  Blocker Ledger                       Readiness / Dependency Graph            │
│  Sequenced Event Journal              Scheduler / Capacity / Priority Policy  │
│                                                                              │
│  Mission Reconciler  (reality → truth)                                        │
│  Mission Observation (read-only status/query) ───────────► Ops Steward         │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         DURABLE EXECUTION                                    │
│ DurableExecution interface                                                   │
│ Current DI runtime ───────────► DBOS candidate adapter / future substrate     │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                  OPERATOR + MODEL ROUTING PLANE                              │
│                        OperatorSession                                       │
│                     Model Routing Policy                                     │
│         ┌────────────────┬──────────────────────┐                            │
│     Pi Adapter      Codex Adapter          Future Adapter                    │
│   GPT Claude Grok Muse   GPT/Claude        local / future model              │
│                                                                              │
│ Provider/model selection is replaceable. Mission authority does not move.    │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│               DOMAIN PROFILE + SKILL PACK                                    │
│ Engineering • Research • Marketing • Ops • Finance • Travel • Future          │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       DI CAPABILITY BROKER                                   │
│      "What bounded capability does this authorized action need?"             │
│                                                                              │
│  Browser │ Research/Doc/Ops │ Engineering Handoff │ Media │ Publishing        │
│                                    │                                         │
│                                    ▼                                         │
│                         ┌───────────────────┐                                │
│                         │       HERDR       │                                │
│                         │ Supervisor        │                                │
│                         │   ↓ Lead          │                                │
│                         │   ↓ Executor ↔ Reviewer                            │
│                         └─────────┬─────────┘                                │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                     CAPABILITY-AWARE WORKER LAYER                            │
│                         Worker Registry                                      │
│      Trusted Mac  │  GPU/Simulator Worker  │  Browser/SaaS Worker            │
│                                                                              │
│ Worker readiness/capability is descriptive. It does not grant authority.     │
└──────────────┬───────────────────┬──────────────────────┬────────────────────┘
               ▼                   ▼                      ▼
            GitHub             Web / APIs            Simulators / GPU
            Repos              SaaS                  Devices / VPN


              ┌─────────────────────────────────────────┐
              │       BREAK-GLASS SIDE CHANNEL          │
              │ Phone → Tailscale → SSH → Trusted Mac   │
              └──────────────────────┬──────────────────┘
                                     ▼
                              Mission Reconciler
                           detects manual reality changes
```

## 2. Mission model

A Mission is one durable piece of work with its own identity, objective, rules,
evidence, budget, status, and authority. It is the unit the whole fabric is
built around: routing finds it, the human authorizes it, the Operator reasons
about it, Herdr may engineer inside it, the Reconciler keeps its state honest,
and delivery happens only through its receipts. One Mission never inherits
another Mission's authority, state, artifacts, approvals, or context.

The v0.7.0 ancestor of a Mission is a DI-REMOTE-2 workflow: a durable `wf-*`
record in the `workflow_authority/` store carrying the Mission Authorization,
the exact human intent, the target identity and baseline, the bound Telegram
placeholder, the target Herdr task id, and the lifecycle phase. The
local-mission path uses the Herdr mission contract (`.herd/state/mission.json`)
with objective, constraints, rules, acceptance criteria, and verification.

### The durable Mission record

The target Mission Manifest is the contract the human approves.

| Field | Meaning |
|---|---|
| identity | A durable `M-####` id, title, mission type, and priority. |
| objective | What done means, in the human's terms. |
| target and baseline | The exact repository, environment, or subject, and the exact revision the mission starts from. |
| allowed and forbidden actions | What the mission may do (inspect, edit an isolated worktree, run tests, benchmark, browser QA, dispatch Herdr) and what it may not (commit, push, PR, merge, tag, release, deploy, change credentials). |
| proof requirements | The evidence that must exist before the mission can be verified. |
| runtime and profile | The Operator runtime, provider, Domain Operator Profile, and Skill Pack. |
| worker requirements | Repository access, browser, simulator, VPN, GPU, or device needs. |
| budgets | Wall clock, model, worker capacity, external resources. |
| stop conditions | What triggers reauthorization, block, or closure: material scope change, credential change, ambiguous external effect. |
| delivery authority | `none`, always, at authorization time. |
| evidence, blockers, artifacts, checkpoints | Ledgers the mission accumulates while it runs. |
| lifecycle | The state below, plus the sequenced event journal. |

The human approves an exact revision. A changed manifest is a new revision and
needs a new approval.

### Mainline lifecycle

```text
CREATED
  -> PREFLIGHT
  -> AWAITING_MISSION_AUTHORIZATION
  -> AUTHORIZED
  -> READY
  -> RUNNING
  -> CLOSING
  -> VERIFIED
  -> COMPLETED
```

PREFLIGHT checks readiness before the human is asked.
AWAITING_MISSION_AUTHORIZATION is where the mission waits for the human card.
READY means the worker, runtime, and capabilities are leased. CLOSING is
budget-aware: stop accepting new work increments, finish the current bounded
writer when safe, preserve evidence and blockers, snapshot the workspace,
create a continuation manifest, settle roles, and end truthfully. VERIFIED is
reached only when the declared proof contract is met and the Reconciler agrees.
COMPLETED follows delivery decisions, which are separately authorized.

The DI-REMOTE-2 workflow lifecycle that exists today is the narrower ancestor,
advanced by the Runtime with a Broker-validated one-shot capability at every
forward transition:

```text
PLANNED -> AUTHORIZED -> WORKSPACE_READY -> PREPARED -> VALIDATED
        -> DISPATCHED -> VERIFIED -> COMPLETED
```

with BLOCKED and NEEDS_REAUTHORIZATION as side states. The Herdr task lifecycle
underneath is `IDLE -> ACTIVE -> COMPLETE`, or `ABORTED`, or `ERROR`.

### Side states

| State | Entered when |
|---|---|
| REJECTED | The human rejects the proposal. |
| NEEDS_REAUTHORIZATION | A material rule change, or in v0.7.0 terms, exceeding the corrective follow-up bound. |
| BLOCKED | A running mission cannot proceed truthfully: policy drift, a missing dependency, a credential problem, a Reviewer loop that will not converge inside scope. |
| PAUSED, RESUMING | Human or scheduler pause where the semantics permit, and the controlled return to RUNNING. |
| ABORTING, ABORTED | A non-terminal mission ended deliberately, with evidence preserved. |
| AMBIGUOUS | A non-idempotent external effect may have happened and its outcome is unknown. Nothing is retried until the Reconciler establishes reality. |
| STALE | The Harness expects a process or a lease to exist and it does not. |

Today BLOCKED and NEEDS_REAUTHORIZATION exist on the workflow record. BLOCKED
is durable and truthful: the historical external-target mountain stopped BLOCKED
at `broker_verification_policy_drift`, a full record stops one workflow with a
truthful capacity code, and an identity-unresolved dispatch that cannot bind
exactly one provable child stops BLOCKED under ruling R-3. Corrective
follow-ups are an authorization-scope bound of 2, not a review-round limit;
exceeding it transitions durably to NEEDS_REAUTHORIZATION. Ambiguous
placeholder and edit outcomes exist as durable adapter states (`indefinite`,
`edit_indefinite`) rather than as a mission state.

### What ends a Mission

AI self-report never completes a Mission. Herdr lifecycle COMPLETE alone can
never verify; the fresh verification turn's `verified_result` is necessary and
never sufficient; and VERIFIED is a Broker-decided conjunction of eight
conjuncts applied against a fresh disk read, the canonical target Reviewer
APPROVE among them as target-produced evidence that the target's own review
process ran and concluded, never as independent verification. A workflow
dispatched before the protected-surface receipt existed fails closed at
verification.

The target generalizes that rule: a Mission is VERIFIED when every proof
requirement declared at authorization has evidence in the Evidence Graph and
the Reconciler confirms that reality matches the durable record. No state is
inferred from "the agent said it finished." Herdr never hands the human a
commit directly; everything comes back through canonical state first.

### Authorization and reauthorization

Approval is one-shot and bound to the exact rendered mission text; a v2
approval dispatches no Operator turn, and the Runtime, a separate process,
claims the durably consumed authorization on its own. A revised plan
invalidates every prior approval in the thread. Replays, mismatches, expiry,
and duplicates fail closed. A DI-REMOTE-1 approval can never authorize a v2
target.

Reauthorization is a first-class state, not an error. When a Mission needs
broader rules than it was granted, it stops, the Attention Router tells the
human, and the human approves a new manifest revision or rejects. Related
Missions (parent and child, dependency, follow-up, supersedes) never
automatically inherit each other's authority. A browser QA Mission that
discovers a bug proposes an engineering child Mission, and that child needs its
own Mission Authorization.

## 3. Interaction and the Grok Bot plane

Every human surface sits behind a `HumanInteractionAdapter`. Grok Bot is the
preferred target conversational plane, with named specialist experiences for
coordination, research, operations, release, browser QA, and incident recovery.
An operational desktop and CLI serve setup, health, and administration. The
visual world is a projection of canonical state and is built last.

The Coordinator Grok Bot is not tied to one Mission. It should be able to
understand:

```text
"What is running?"
"What needs me?"
"Where is the Worker mission?"
"What did Research Bot find?"
"Ask Engineering Bot whether that changes our implementation."
"Go back to the Zenmo marketing mission."
"Which missions are blocked?"
"What is ready for commit?"
```

and hold many Missions at once:

```text
Coordinator Grok Bot

├─ Mission 142 — Worker seam
│  └─ Engineering Bot
│
├─ Mission 143 — DBOS research
│  └─ Research Bot
│
├─ Mission 144 — Zenmo launch
│  └─ Marketing Bot
│
└─ Mission 145 — SaaS operations
   └─ Operations Bot
```

A specialist bot can participate in more than one Mission, and a Mission can
involve more than one specialist bot. Bots exchange Mission references,
artifact references, evidence, questions, summaries, and recommendations. They
do not exchange hidden authority, and the canonical truth always comes from
Dodging Infinity rather than from a bot's memory of the conversation.

Today the `HumanInteractionAdapter` seam is on `main` and the production
Telegram controller routes transport operations through
`TelegramHumanInteractionAdapter`. Durable cursor and queue state, approval
validation, Mission Authorization, result-delivery state, `OperatorSession`,
Runtime, Herdr, and Git authority remain outside the interaction seam.
Telegram is the current transport and stays the reference and the fallback
until the Grok adapter is proven; what it does today is in
[operations.md](operations.md#9-telegram-remote-control).

A surface can carry an exact human authorization. It cannot mint one. That rule
is section 11.

## 4. Routing

Two routers, two directions, never combined.

The **Mission Router** answers "which Mission is this?" for inbound
conversation. It prefers deterministic evidence, in order: an explicit Mission
id, a reply-to binding, an approval or result binding, an exact repository or
issue reference, a known project, a durable alias, a unique contextual match,
and only then a bounded fresh routing turn. Its outcomes are closed:

```text
existing_mission: M-0042
new_mission
clarification_required
```

If a follow-up could refer to several Missions, it asks rather than guesses. It
routes identity only; it does not engineer.

The **Attention Router** answers "what needs a human now?" for outbound
attention: authorization ready, clarification needed, Mission blocked, Reviewer
rejected, verification failed, delivery ready, ambiguous effect needing a
decision, credential action required. The human should not have to poll to
discover that something needs attention.

**Bot coordination** is the third routing question — which specialist bot
should participate — and it is answered against the Mission registry, not by a
bot deciding for itself.

Today, durable `wf-*` workflow identity, Telegram message binding, target
identity, and task identity exist and survive independently of the Gateway
turn. The first-class `M-####` registry and the natural-language router remain
open, as Iteration 1 on the [roadmap](roadmap.md).

## 5. Mission Harness

The Mission Harness is the authoritative system. It owns Mission identity, the
Mission Manifest and its Authorization, the Authority Ledger, the lifecycle
state machine, the Evidence Graph and proof requirements, the Blocker Ledger,
the Artifact Registry and delivery receipts, the Action Risk Envelope, budgets
and closure policy, checkpoints and recovery state, the readiness dependency
graph, the Scheduler, the deterministic Reconciler, the Observation Service,
and a sequenced event journal with snapshots.

If Grok, Pi, Codex, Herdr, a desktop window, or a worker disappears, the
Harness is still the source of truth.

The tree has a narrower ancestor of this: the durable `workflow_authority/`
store (schema-2, atomic, cross-process-locked, fail-closed), the DI-REMOTE-2
workflow lifecycle PLANNED through COMPLETED with BLOCKED and
NEEDS_REAUTHORIZATION, the closed-schema Mission Authorization, and a Runtime
that advances the lifecycle through Broker-validated one-shot capabilities.

### Durable execution

Dodging Infinity should not rebuild low-level durable workflow machinery unless
it has to. A `DurableExecution` interface (start, enqueue, schedule, cancel,
resume, checkpoint, inspect, recover) sits between the Harness and whatever
substrate provides persistence, queues, retries, and recovery. The substrate is
infrastructure. The Harness still owns Mission meaning, authority, evidence,
routing, and completion rules.

DBOS is the first candidate substrate, and PostgreSQL is a potential dependency
through DBOS. Neither is selected and neither is depended on. The initial
`DurableExecution` seam exists in this checkout; the substrate adapter does
not.

## 6. Operator and model routing

The Operator is the reasoning role for one Mission. It understands the Mission,
examines evidence, decides what bounded work is needed, invokes approved
capabilities, and coordinates engineering when necessary. It is a role, not a
model brand.

Model routing sits inside the orchestrator, below Mission authority:

```text
                     Mission / Step
                          │
                          ▼
                    OperatorSession
                          │
                          ▼
                  Model Routing Policy
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
      Pi Adapter      Codex Adapter      Future
         │                │
    ┌────┼────┬────┐      ├────────────┐
    ▼    ▼    ▼    ▼      ▼            ▼
   GPT Claude Grok Muse   GPT         Claude
```

Routing can consider mission type, task type, reasoning depth, latency, cost,
context window, multimodal requirement, tool compatibility, provider health,
privacy constraints, user preference, and fallback policy. But `Mission 142`
never becomes `"Grok Mission 142"`. The provider is replaceable; the Mission is
not.

Responsibilities are separated from models throughout. The outer Operator and
the four Herdr roles are logical responsibilities rather than permanent model
assignments:

```text
Supervisor  -> Model A
Lead        -> Model B
Executor    -> Model C
Reviewer    -> Model D
```

or the same model for all four. The orchestration contract belongs to the role.
Presets such as `max-quality`, `all-claude`, and `conservative` are convenience
configurations, not architectural requirements; they assign runtimes, models,
and permissions and do not alter the orchestration hierarchy.

### The OperatorSession seam

`OperatorSession` is the provider-neutral boundary around the Operator role.
What exists in this checkout is the initial two-step abstraction, in the
`operator_session/` package:

- `OperatorSession` is an abstract two-step boundary between a caller (a
  transport adapter, a CLI) and whatever operator provider carries the turn.
  `prepare(text, repository, session_id=None, source="terminal")` builds the
  provider request through a subclass hook, validates that the request carries
  a non-blank string request id, captures that id as a frozen field, records
  the turn in a per-session weak set, and returns a `PreparedTurn`. It makes no
  provider call and writes no state. `execute(prepared)` validates first (it
  must be a `PreparedTurn`, produced by this session's own `prepare`, whose
  request still reports the same request id) and only then makes exactly one
  provider call with the original request object, returning the provider's
  result unchanged. It retries nothing, swallows nothing, invents no status,
  and does not consume the turn.
- `PreparedTurn` binds the request, by reference, to the session that built it.
  Equality is identity: a turn is the turn `prepare` minted, never a
  value-equal look-alike. An ordinary construction fails provenance.
- `PreparedTurnError` means the turn cannot be executed and no provider call
  was made.
- `FunctionOperatorSession` adapts two injected callables.
- `CodexOperatorSession` is the package's only provider import. It resolves
  `build_request` and `submit` as attributes of the `codex_gateway.gateway`
  module at call time, never at import time, so the gateway module remains the
  single point of substitution.

The seam owns request construction, provenance, and request-identity stability
across the prepare-to-execute gap. It owns nothing else: no authority of any
kind, no orchestration or workflow lifecycle, no retry policy, and no
interpretation of the provider `session_id`, which flows through as opaque
continuation state.

The target lifecycle is wider:

```text
OperatorSession
  create()
  prompt()
  steer()
  follow_up()
  abort()
  status()
  events()
  restore()
  close()
```

`create` opens a session bound to one Mission and its manifest. `prompt` and
`follow_up` deliver bounded work. `steer` adjusts an active session within its
authorized scope. `abort` ends it with evidence preserved. `status` and
`events` are read-only views the Observation Service can consume without
interrupting the session. `restore` rebuilds a session from durable state after
an Operator process disappears, which is what lets the Reconciler recover a
Mission without a human. `close` settles it. None of these exist in the tree;
`prepare` and `execute` are not two of them, but the narrower seam the target
lifecycle will sit behind.

### Providers

| Provider | Role in the design |
|---|---|
| Codex | The current Operator implementation, reached through the Codex Gateway, and the fallback until another path is proven. Not the role itself, and not permanent architecture. |
| Pi | The preferred candidate provider-neutral operator runtime. An RPC boundary is the preferred first integration, so Pi can supply its model and tool runtime without owning Mission identity, authority, evidence, or delivery. Not selected, not a dependency. |
| GPT, Claude, Grok models, Muse | Potential model providers behind the OperatorSession. Grok as a model is separate from Grok Bot as an interaction plane. |
| Local and future models | Same boundary, including privacy-constrained work that must stay local. |

A provider-neutral session makes it possible to compare models while holding
the Mission rules constant. No provider is a permanent assignment to any role.

### Domain Operator Profiles and Skill Packs

A Domain Operator Profile gives a Mission reusable specialization: a Skill
Pack, context sources, proof requirements, worker requirements, an escalation
policy, and a version. A Skill Pack encodes how to approach a class of Mission
— for an engineering bug fix: investigate, plan, review, security, QA; for
research: source discovery, source evaluation, comparison, synthesis,
adversarial review, artifact generation; for automation assessment: process
reconstruction, pain-point analysis, system-of-record mapping, opportunity
scoring, ROI, pilot design, adversarial review; for browser QA: snapshot,
interaction, visual proof, console and network inspection, regression evidence;
for release preparation: release-doc reconciliation, diff inspection,
verification, artifact packaging, delivery preparation.

Profiles improve reasoning. Skill Packs guide reasoning. Neither inherits or
grants authority.

## 7. Capabilities

A Capability is a specific thing a Mission is allowed to do: inspect a
repository, edit an isolated worktree, run tests, benchmark, drive a browser,
call an external API, dispatch Herdr. Every capability is brokered by Dodging
Infinity against the Mission's authorization at the moment of use. A capability
being installed on a worker does not mean a Mission may invoke it.

An **Action Risk Envelope** classifies each proposed external action by
confidence, blast radius, reversibility, external effect, credential scope,
ambiguity risk, and data sensitivity. The classification can raise required
review, supervision cadence, rollback and reconciliation requirements, model
choice, and human escalation. It cannot create missing authority.

The ancestor in the tree is the v0.7.0 Target Broker: privileged,
fixed-action, nine fixed lifecycle actions, each performed only against a
Runtime-minted one-shot capability bound to exactly one
`(workflow_id, action, revision)` tuple. Sensitive values are resolved from the
protected workflow record and never supplied by the caller. Capabilities are
minted by the Runtime, never by Codex.

The Broker has nine fixed lifecycle actions, each performed as
`(workflow_id, action, revision, capability)` with a Runtime-minted one-shot
capability bound to exactly that `(workflow_id, action, revision)` tuple;
sensitive values are never supplied by the caller, and capabilities are
minted by the Runtime, never by Codex.

### BrowserCapability

BrowserCapability puts a persistent browser behind Dodging Infinity's authority
and splits its operations into three classes:

```text
Operator
   |
DI BrowserCapability
   +-- READ:   snapshot, text, screenshot, console, network
   +-- WRITE:  navigate, click, fill, upload, submit
   +-- HUMAN HANDOFF:  MFA, CAPTCHA, manual takeover
           |
     Playwright / Chromium
```

Reads produce evidence and carry no external effect. Writes are external
effects and are authorized, recorded, and reconciled as such. Human handoff
covers MFA, CAPTCHA, and takeover, and never tries to automate past them. Stale
element references fail rather than guess. Screenshots, snapshots, console
health, and network traces are first-class evidence in the UI proof contract.
Playwright is the planned dependency behind it. pstack is the design reference
for the persistent-browser, element-reference, QA, and skill-pack ideas; its
authority model is not inherited and it is not a dependency.

Browser writes follow the same ambiguity model as every other non-idempotent
external effect. For each write, the capability records the Mission, target,
action, pre-state, risk classification, execution receipt, post-state, and
reconciliation result. If the effect is uncertain — the process crashed after
submit, the network dropped mid-request, the page shows a duplicate mutation —
the Mission blocks further submit attempts, enters AMBIGUOUS, and the
Reconciler establishes reality before any retry is considered. A browser QA
Mission that finds a duplicate mutation in a network trace does not press the
button again to see what happens.

### Non-engineering Missions

Research, document, and operations tools are bounded Mission capabilities of
the same kind. A research Mission runs the research Skill Pack — source
discovery, credibility checks, comparison, synthesis, adversarial review —
collects direct evidence through BrowserCapability in read mode, and returns a
registered artifact rather than chat text:

```text
You:
"Compare DBOS, Temporal, and Postgres
for durable execution."
          │
          ▼
Research Mission
          │
      ┌───┴───┐
      ▼       ▼
  Research   Browser
      │       │
      └───┬───┘
          ▼
       Evidence
          │
          ▼
  Research artifact
          │
     ┌────┴──────────────┐
     ▼                   ▼
    You           Engineering Mission
```

A marketing Mission produces campaign artifacts and stops: publishing stays
unauthorized. An automation-assessment Mission reconstructs a current-state
process, identifies where humans are acting as middleware between systems that
already hold the data, and returns an ROI model and a pilot architecture, with
production systems, customer communications, and operational records all
forbidden. The shape is the same in each case: declared proof, bounded
capabilities, registered artifacts, no delivery authority.

## 8. Engineering: Herdr

Herdr is the engineering organization inside an engineering Mission. When a
Mission requires software engineering, the handoff creates an isolated Herdr
with four roles: Supervisor, Lead, Executor, and Reviewer. Herdr starts from a
bounded handoff, which the Operator has already translated from human intent,
and turns it into independently reviewed engineering work with observable
verification evidence. The Supervisor decomposes further inside that boundary;
Herdr never receives an unbounded objective.

Herdr is not the Mission control plane. It does not own Mission identity,
authority, lifecycle, evidence policy, scheduling, or delivery receipts.
Dodging Infinity governs the Mission; Herdr engineers inside it. Herdr is also
not the product: a Herdr UI would be a UI for the engineering organization, not
for the fabric. Each Herdr owns a finite scope: one repository, one top-level
task, one effective rule set, one runtime state, explicit acceptance criteria,
and observable verification evidence. The core loop is understand, define,
decompose, solve, challenge, validate, deliver.

```text
Herdr Handoff
      |
      v
Supervisor
      |
      v
Lead
   /      \
  v        v
Executor  Reviewer
   \        /
    \      /
      v
Verified Result
```

- The Supervisor is the first strategy-bearing component. It owns
  decomposition, role assignment, execution planning, sequencing, engineering
  strategy, and the validation workflow. The Mission handoff binds destination
  and boundaries; the Supervisor determines the engineering route.
- The Lead owns acceptance and completion. It briefs the Executor, validates
  the Reviewer's decision with `herdctl review-decision`, and persists it.
- The Executor implements the smallest coherent change that satisfies the
  brief, runs the verification command, checks its own diff, and reports
  evidence rather than confidence. It stays in the same session across
  rejection rounds.
- The Reviewer challenges the result independently. It is read-only, it does
  not need write access to `.herd/state/`, and it returns exactly one canonical
  token.

Bootstrap boundaries are explicit: a role is prevented from inferring
engineering work from repository state, verification commands, or shared memory
before it receives an explicit task or delegation. Long-running rejection and
correction loops are intentional when they keep producing useful engineering
evidence.

### The engineering handoff

The Operator, not the human, normally constructs the handoff. A valid handoff
carries the objective, repository context, known constraints, applicable rules,
desired outcome, and unresolved questions. It does not carry the engineering
execution plan; that belongs to the Supervisor. Prefer "investigate and resolve
the authentication timeout; backward compatibility required; no schema change;
add regression coverage; verify the existing suite" over "modify file X, add
function Y." The Operator defines the destination and Herdr determines the
route.

In the DI-REMOTE-2 path, the Broker's first dispatch is the byte-exact stored
handoff, and a corrective follow-up is a bounded corrective brief, never a
technical solution. The target Herdr bootstraps unattended inside the isolated
managed workspace, with all four roles registered and interactive-ready before
engineering proceeds.

In the target design, multiple Missions each receive their own Herdr Pod. There
is no global engineering Supervisor; Dodging Infinity coordinates the Missions
and each Mission's Supervisor coordinates engineering inside it.

### Reviewer independence

The Reviewer is a separate role in a separate session, ideally on a different
model. The `max-quality` preset pairs a Claude Executor with a GPT Reviewer for
that reason, and the v0.7.0 certification herd ran its independent Reviewer on
`gpt-5.6-sol` while Supervisor and Executor ran on `claude-fable-5-1`. The
Reviewer contract requires exactly one canonical terminal decision,
`HERD_DECISION: APPROVE` or `HERD_DECISION: REJECT`; synonyms are not accepted.
The Lead validates the decision with `herdctl review-decision`, which reads the
Reviewer's transcript, records a new round, and persists the review file;
malformed output returns `"valid": false` and the Lead re-prompts the same
Reviewer session rather than interpreting it. Rejection can cause additional
engineering iterations until the work genuinely satisfies the Mission.

### What a Reviewer APPROVE does and does not mean

An APPROVE means the target's own review process ran and concluded. It is
target-produced evidence, and in v0.7.0 verification it is one of eight
conjuncts, never independent verification. Herdr lifecycle COMPLETE alone can
never verify a workflow, and the fresh verification turn's `verified_result` is
necessary but never sufficient.

The historical external-target mountain is the concrete case: target Herdr task
`20260830-094026-9fef2d` reached COMPLETE and a canonical target Reviewer
APPROVE was recorded, and the workflow still correctly terminated BLOCKED at
`broker_verification_policy_drift` because the Broker's verification gate found
genuine post-dispatch policy drift. A gate that took APPROVE as sufficient
would have delivered.

The target keeps that rule and generalizes it: Reviewer approval is one proof
requirement among those declared at authorization, the Evidence Graph records
it, and Mission verification is a separate Reconciler-confirmed state. Reviewer
APPROVE matters. It is never, by itself, authoritative Mission verification.

### An engineering Mission end to end

```text
You:
"Fix Mitiq issue #2802.
Do not ship it."
          │
          ▼
Dodging Infinity creates the Mission
          │
          ├─ target repository
          ├─ objective
          ├─ constraints
          ├─ proof requirements
          └─ no delivery authority
          │
          ▼
You approve
          │
          ▼
Engineering Capability
          │
          ▼
Herdr
          │
Supervisor → Lead → Executor ↔ Reviewer
          │
          ▼
tests + evidence
          │
          ▼
Dodging Infinity verifies
          │
          ▼
result comes back to you
```

Fixing the issue did not authorize a commit, push, merge, release, or
deployment. Each of those is its own decision, in section 11.

The same shape survives a harder day. A customer reports that large CSV exports
time out; you are travelling with a phone. The fabric drafts the Mission at the
exact current revision and proposes the rules — allowed: inspect, isolated
edits, tests, benchmark, browser QA, a Herdr Pod; locked: commit, push, merge,
release, deploy, credentials — and lists the proof required. You approve, and
only then is execution authorized. The Operator traces the export path and
finds the service buffers the whole CSV before sending a byte. Round one of the
Herdr Pod is rejected for leaking a resource on early disconnect; round two
fixes it; the Reviewer approves. BrowserCapability runs a real export and
records baseline against candidate. Your flight boards and the Mission keeps
running. The Operator process disappears, the Reconciler detects it and
restores the bounded session from durable state, with no human intervention.
You ask what is happening and the Observation Service answers from canonical
state without interrupting Herdr. Reviewer approval, tests, benchmark, browser
QA, and authoritative verification all become evidence, and the Mission becomes
VERIFIED. Then, and only then, you inspect the prepared commit and approve
exactly it — with push, merge, and deployment each still separately locked.

## 9. Workers

A Worker is a capability-aware bounded execution host with declared
capabilities, credential classes, capacity, and leases:

```yaml
worker_id: W-001
health: READY
os: macos
arch: arm64
capabilities:
  repos: [customer-platform]
  browser: true
  ios_simulator: true
  gpu: false
  vpn: [corp-a]
credential_classes: [github-dev, staging]
capacity:
  herdr_pods: 3
  browser_sessions: 2
leases: [M-1842]
```

The Scheduler matches a Mission's worker requirements against declared
capabilities and capacity, and the lease binds the match for the Mission's
lifetime. Readiness (READY, DEGRADED, BLOCKED, STALE) is checked before
consequential dispatch, and a required dependency known to be unavailable fails
the Mission before it starts rather than partway through.

```text
Mission:
"Run the quantum simulation."

Requirements:
GPU
simulator
repo access

Worker Registry:

Worker 1 — Trusted Mac
repo access = yes
GPU = no

Worker 2 — GPU Host
repo access = yes
GPU = yes
simulator = yes

Worker 3 — Browser Worker
GPU = no
```

The Scheduler selects Worker 2. Worker selection answers where the work can
run; the Mission already defines what the work is allowed to do. The Worker
does not authorize the Mission, and worker readiness is descriptive rather than
permission-granting.

The **Worker Registry** is the durable list of workers, their declared
capabilities and credential classes, their capacity, their leases, and their
health. It is written by workers registering and by the Reconciler observing
them, and read by the Scheduler.

Today every component from the adapter onward runs on the trusted Mac. It owns
the repositories, Git and Codex credentials, the Gateway, the Herdr runtime and
agents, local test environments, `.herd` state, and the commit and push gates.
The Mac is Worker 1, not a permanent singleton: later workers may be a Linux
GPU host, a VPN-connected host, a browser-only host, or a simulator host.
Nothing about the authority model changes when a second worker appears, and the
Mac's current possession of every credential is a fact about Worker 1 rather
than a rule of the fabric. External systems — GitHub, web and SaaS APIs,
simulators, devices — are reached only through capabilities on a worker.

## 10. Evidence and verification

A Mission declares the proof it must produce before it is authorized. The proof
requirements are part of the Mission Manifest the human approves, so "done" is
defined by the human and the contract, not discovered afterward by the model. A
Mission that cannot meet its declared proof does not become VERIFIED; it ends
truthfully as BLOCKED, or it returns for reauthorization with a narrower or
different contract. Evidence declared late is not evidence.

The narrower version already enforces the principle: a mission contract carries
explicit acceptance criteria and a verification command into Herdr; a
DI-REMOTE-2 Mission Authorization binds acceptance and execution scope before
approval; and dispatch stamps a protected-surface receipt so that a workflow
dispatched before the receipt existed fails closed at verification.

### Proof contracts

| Class | Required evidence |
|---|---|
| Code | The diff; regression tests; focused tests; adversarial checks; Reviewer decision; authoritative verification against the full suite. |
| UI | Before and after screenshots; an interaction trace; console health; network health; Reviewer decision. |
| Performance | A baseline measurement; a candidate measurement; a stated tolerance; Reviewer decision. |
| Artifact | An exact digest; an allowed type; a size bound; containment; Reviewer decision. |

Each item becomes a node in the Evidence Graph with its producer, its inputs,
and its digest. A Reviewer decision is one node, not the graph.

Today the code contract is the one that runs: Herdr verification commands,
focused regression, the full suite, and a canonical Reviewer decision, with the
results recorded in the task state and the review files under
`.herd/state/reviews/`. UI and performance contracts have no machinery in the
tree.

### Verification is not self-report

AI self-report never completes a Mission:

- A free-form model message is never reinterpreted as an approved plan or a
  verified result; the Operator answers through a versioned envelope (plan,
  status, result, error).
- Herdr lifecycle COMPLETE alone can never verify a workflow.
- The fresh verification turn is a separate read-only process that consumes a
  bounded, streamed, read-only evidence projection rendered into its prompt.
  Its `verified_result` is NECESSARY, NEVER SUFFICIENT.
- VERIFIED is a Broker-decided conjunction: eight conjuncts (ten independent
  problem codes) applied against a fresh disk read.
- Observation completeness is SOURCE-SCOPED (ruling R-6): a decision is blocked
  only by a demoting diagnostic in its registered consumed-source set; the raw
  global completeness is recorded and rendered unaltered; an agents-unprobed
  global PARTIAL is expected in production.

### The gates between COMPLETE and a delivered result

1. **Dispatch identity.** An identity-unresolved dispatched workflow runs one
   fresh `status_recovery` turn and the evidence-only `reconcile_dispatch`
   action, which binds exactly one provable existing child (exact
   leased-workspace realpath plus the lease's own observed task id) or stops
   durably BLOCKED. Under ruling R-3 it reads nothing outside this repository,
   the derived alias is never binding evidence, and more BLOCKED outcomes are
   the accepted cost.
2. **Observation.** The Runtime observes the target through the read-only Herdr
   observability; the herd's own stopped set drives the stopped decision under
   source-scoped completeness, and a projection degraded in a consumed source
   WAITS.
3. **Verification.** The fresh verification turn, the eight-conjunct gate, and
   the protected-surface receipt check.
4. **Delivery.** The verified result edits the bot-owned placeholder bound
   before dispatch, exactly once. Terminal delivery states are disclosed rather
   than retried.

Failed or ambiguous states fail closed durably and surface through `/status`
with concrete remedies. A record at a hard bound stops that one workflow with a
truthful capacity code and never kills the Runtime.

### The Evidence Graph

The Evidence Graph is the durable record of what was tested, reviewed,
measured, or verified, per Mission: nodes for each proof item, edges to the
artifacts, commands, and roles that produced them, digests throughout. It is
what the Observation Service reads to answer "what did Reviewer say?" and "what
is still pending?" without asking the Operator. It is what the Reconciler
compares against reality. It is what the delivery ceremony shows the human
before an exact commit is approved. Nothing in the tree implements it; today
the equivalents are task state, review files, the workflow record, and the
`herdctl observe` projection.

## 11. Authority

Humans are the root of consequential authority, and nothing else mints it.
Everything else in this document depends on that rule.

### Intent is not authority

A natural-language request communicates intent. A Mission Authorization
communicates permission. They are different things, and the code keeps them
apart: a Telegram request produces a closed-schema Mission Authorization
through a separate fresh planning turn; the human approves that exact rendered
text once, on the bound message; and the durable store consumes the approval.
Typed chat text never carries authority, and a marker-bearing user line is
visibly quoted before forwarding so it can never forge the adapter's decision
envelope. The user's exact typed message is stored verbatim in the workflow
record, adapter-stamped, bound by its own sha256, and rendered into the
approved mission text, which is what makes "the Operator can never change what
the human said" true.

### What a Mission Authorization is

Today a Mission Authorization is a closed-schema document that binds the
destination and its boundaries only: objective, constraints, rules, desired
outcome, acceptance, unresolved questions, execution scope, control identity
and policy digest, canonical target, issue or PR, approved baseline, bounded
handoff, revision, `delivery_authority: none`, and the exact human request.
Implementation-strategy keys are refused by normalized name at any nesting
depth; the target Herdr Supervisor owns the engineering route. Validation is
structural, and [SECURITY.md](../SECURITY.md) states the limit of that and the
protections around it.

The target Mission Authorization is the human approval of an exact revision of
a Mission Manifest: objective, target and baseline, allowed and forbidden
actions, proof required, budget, priority, worker requirements, runtime and
profile, Herdr topology, stop conditions, and `delivery_authority = none`. The
human sees the whole card and approves, edits the rules, or rejects.

### The separate authorization chain

Mission authorization does not authorize a commit. A commit does not authorize
a push. A push does not authorize a merge. A merge does not authorize a
release. A release does not authorize deployment.

The machine path holds no delivery authority anywhere: remote Missions carry
`delivery_authority = none`, the Runtime is structurally incapable of delivery
(no subprocess outside the pinned read-only git transport seam, no delivery
verb in the package, enforced by literal scan and subprocess confinement), and
commit, push, and release tag are three separate local human gates with their
own one-shot approvals. The commands are in
[operations.md](operations.md#11-git-approval-gates).

Consequential authorization targets exact, one-shot, action-bound,
revision-bound, mission-bound, human-authorized, expiry-bound, replay-resistant
receipts, for each of prepare commit (read-only), approve commit, approve push
or open PR, approve merge, approve tag, approve release, and approve deploy. No
delivery action inherits authority from the Mission Authorization or from
another delivery action. Every attempt writes a durable receipt (`prepared`,
`authorized`, `executing`, `succeeded`, `failed`, `ambiguous`) and reconciles
uncertain external effects before another attempt is allowed.

The pull-request link of that chain is implemented (P1-A6, `pr_delivery/`). A
PR Delivery Authorization is a separate durable human authority record, minted
only by a local terminal ceremony in which the human types the exact reviewed
candidate identity. It binds the candidate's changed paths, statuses, modes
and content digests; the recorded Herdr COMPLETE, canonical Reviewer APPROVE
and independent verification evidence; the repository, remote, source and base
branches, baseline and committer; the closed action set BASE_REFRESH, COMMIT,
PUSH, PR_CREATE; an absolute expiry; and a revocation state. Trusted system
logic derives one-shot receipts from it, and the installed git guards accept an
exact executing receipt as a second path beside the manual `approve-commit`
and `approve-push` tokens, which stay as they are. A disjoint fast-forward
advance of the base is refreshed automatically with the candidate identity
re-proven; every effect reconciles after a crash without a duplicate; delivery
stops at an open pull request, and no merge, tag, release, deploy or publish
verb exists on that path. It does not read Herdr state and cannot be driven by
a model, a transport, a Worker or a Capability.

### The Authority Ledger and remote authorization

The Authority Ledger is the durable record of every authorization and its
consumption. It is what lets a consequential action be approved from a phone
without weakening the gate:

```text
You:
"Commit it."
    │
    ▼
Grok Bot
    │
    ▼
Dodging Infinity
    │
    ▼
Exact Action Proposal
    │
    ▼
You approve
    │
    ▼
Authority Ledger
    │
    ▼
One-shot durable authorization receipt
    │
    ▼
Trusted Worker
    │
    ▼
Local guard verifies receipt
    │
    ▼
Commit executes
```

The approval binds the Mission, repo, branch, HEAD, staged diff hash,
operation, remote or target, human identity, timestamp, expiry, and one-shot
use. A merge approval binds the exact PR, head SHA, base, merge method, and the
required checks and reviews state, and the ceremony states them back before you
approve:

```text
Grok Bot:
"PR #72 is ready.
Head: abc123
CI: green
Merge target: main
Merge commit will include exactly this reviewed head.
Approve merge?"
```

This removes the need to type a second approval command locally. It does not
remove enforcement. Remote delivery authority is not implemented today: no
Telegram message, plain text or approval callback, can commit, push, open a PR,
tag, release, or deploy, and the adapter's decision envelope says so.

### What a transport, a model, and a UI cannot do

- A **transport** can carry authority but cannot mint it. Telegram carries a
  one-shot button approval bound to the exact rendered Mission; it cannot
  commit, push, open a PR, tag, release, or deploy.
- A **model** can request authority but cannot grant it. The Operator proposes
  a plan or a Mission Authorization; the Runtime mints the one-shot
  capabilities, never Codex; the Broker validates and consumes them.
- A **UI** can display authority but cannot create it. `/status` renders
  durable state; it changes nothing. The target visual world reads canonical
  state and never approves, completes, or delivers.
- **Credential availability does not grant Mission permission.** A worker
  declares credential classes it holds; a Mission declares the classes it
  needs; the Scheduler matches them and the authorization binds the match. A
  worker having a credential does not mean every Mission may use it, and a
  capability being installed does not mean a Mission may invoke it.
- **Priority** changes how closely a Mission is watched. It never changes
  authority, credential scope, Mission scope, Git gates, deployment gates, or
  allowed side effects.

This list also covers Grok Bot, Pi, GPT, Claude, Grok models, DBOS, Skill
Packs, Domain Profiles, the Ops Steward, the Reconciler, BrowserCapability,
Herdr, worker machines, the desktop admin, and the visual world. None of them
may create missing authority.

### Trust boundaries today

**Phone and Telegram.** A remote human interface. It may submit intent, receive
plans, approve or reject bounded plans, query Mission status with `/status`
(each answer explicitly requested and queued behind active Gateway work, never
streamed), receive restart and recovery notices about work it sent that was
interrupted, and receive verification results. It must not receive arbitrary
shell access, invoke Herdr directly, construct Herdr missions itself, bypass
Operator reasoning, silently broaden permissions, expose Mac credentials or
repository secrets, or authorize commits, pushes, PRs, tags, releases,
deployments, or merges.

**MacBook.** The trusted execution node. It owns the repositories, Git
credentials, Codex credentials and session state, the Codex Gateway, the Herdr
runtime and agents, local test environments, repository-scoped `.herd` state,
and the commit and push authorization gates. All engineering execution stays
local to this node unless a future architecture explicitly changes that
boundary; the target Worker model does, by making the Mac Worker 1 rather than
the only host.

**Codex Gateway.** A transport-neutral front door. It accepts human intent,
validates the target repository, starts or resumes Operator sessions, returns
structured results, preserves source and request identity, and fails closed on
malformed output. It does not import Herdr, call `HerdrControlPlane`, invoke
`herdctl`, prompt Herdr agents, create Herdr missions, dispatch engineering
work, grant approvals, commit, push, merge, or release. The isolation is
enforced by the static suite.

**Operator.** Owns the human-to-engineering boundary: it understands intent,
inspects the target repository, gathers context, resolves genuine ambiguity,
proposes a bounded plan, receives human approval, creates the Herdr handoff,
dispatches and monitors Herdr, handles routine recovery inside the approved
scope, independently inspects the result, creates bounded follow-up work when
necessary, prepares verified work for delivery, and requests protected human
authorization. It does not become the Executor.

**Herdr.** Owns engineering. The Supervisor determines the engineering route
and owns decomposition, role assignment, execution planning, sequencing,
strategy, and the validation workflow. The Lead owns acceptance. The Executor
implements. The Reviewer adversarially validates and can reject until the work
satisfies the Mission. Herdr does not hold delivery authority and does not
govern the Mission.

**Human.** The ultimate delivery authority. Normal engineering execution
requires one bounded-plan approval. Delivery is separately protected: the human
explicitly authorizes commit, push, pull-request publication where required,
tag, release, and any destructive or materially expanded authority.

### Ambiguous external effects

If a non-idempotent external effect may already have happened, the system does
not blindly repeat it. An ambiguous placeholder creation fails closed before
dispatch; dispatched-but-unconfirmed Gateway work is reported AMBIGUOUS after a
crash and never replayed; an ambiguous edit outcome on the bound result message
is retried only as an idempotent edit of that same message; and a
placeholder-bound workflow can never fall back to a second delivery call.
"Exactly once" means never twice and never silently dropped, not that the
result always eventually arrives; the terminal states are disclosed in
`/status`, and recovering one is a human step.

The same rule applies to browser form submissions, API mutations, Git pushes,
release creation, deployment triggers, and every other non-idempotent action.
When certainty is lost the Mission enters AMBIGUOUS, the deterministic
Reconciler checks reality, and only after reconciliation can the system decide
whether a retry is safe.

### Break-glass remote access

Normal Mission control and break-glass access are different paths:

```text
Normal mission control        Alternative / break glass

Phone                         Phone
  │                             │
  ▼                             ▼
Grok Bot                      Tailscale
  │                             │
  ▼                             ▼
Dodging Infinity              SSH
  │                             │
  ▼                             ▼
Worker                        Trusted Mac
```

The break-glass path exists so that a broken transport does not strand the
host: if Telegram dies completely while you are away, you can still reach the
trusted Mac and recover it. It is a recovery path, not normal Mission
authority. If you manually change Git state, Herdr state, services, processes,
config, or a workspace over SSH, the Reconciler updates or blocks the relevant
Mission so Dodging Infinity does not continue on stale assumptions.

## 12. Observation, reconciliation, and recovery

Canonical state, not conversation, not a model process, and not a UI, is the
durable truth. If Grok, Pi, Codex, Herdr, a desktop window, or a worker
disappears, the Mission Harness is still the source of truth, and every surface
reconstructs from it. A status answer comes from canonical state. A visual
world reconstructs from the registry and the event journal. A missed UI event
is repaired by a snapshot and an event catch-up, never by asking an agent what
happened.

The tree already holds to this for what it has. The durable
`workflow_authority/` store is the truth for a DI-REMOTE-2 workflow; `/status`
reads it while the Mission continues independently, without waiting for the
Mission's Operator turn. Adapter authority-bearing state is persisted before
any external action. Task state, review files, and the observation projection
are on disk under `.herd/`.

### Observation is pull-based and read-only

`herdctl observe` builds a strictly read-only point-in-time projection of a
repository's Herdr, schema version 3, with a `completeness` field that
describes visibility only. It does not mutate, repair, prompt agents, change
workflow, or control execution. Observation is a reporting surface, not a gate.
Bounds are constants, disclosed rather than silently presenting partial
information as complete. `herdctl health` is the read-only readiness probe
beside it. The schema contract and the limits of the evidence are in
[operations.md](operations.md#7-status-observation-and-health).

The Observation Service generalizes that to Missions. A human asks "what is
happening with M-1842?", "what did Reviewer say?", "what changed in the last
twenty minutes?", "show me everything blocked", "what is waiting on me?". Those
queries are read-only. They do not stop the Operator, steer the Operator,
interrupt Herdr, consume engineering context to answer status, or invent
progress percentages. The answer is elapsed time, Operator health and last
activity, Herdr role states and review round, current work, evidence status per
proof item, worker, budget, blockers, and last meaningful progress, all from
canonical state.

Observation handles pull; the Attention Router handles push. Today the only
unsolicited message class is a restart or recovery notice about work the user
sent that was interrupted. There is no proactive progress streaming.

### The Reconciler

The Reconciler is deterministic operational machinery, not an AI agent. It
continuously compares what the Harness expects to be true with what is actually
happening: Operator health, Herdr task state, Reviewer state, CI and check
state, target drift, worker state, browser effects, artifact state, result
delivery, external delivery receipts, human approvals, budget, blockers. Its
outcomes are closed:

```text
Operator expected ACTIVE + process missing   = STALE, restore
Herdr COMPLETE + no Reviewer decision        = not complete
CI red                                       = preserve failure, diagnose
target changed                               = BLOCKED, reauthorize
browser effect uncertain                     = AMBIGUOUS, reconcile
human approval pending                       = Attention Router
```

The tree has deterministic reconciliation for specific cases, not a general
Reconciler: dispatch identity recovery under ruling R-3, crash-after-edit
recovery against the bound result placeholder, the stale `ACTIVE` to `COMPLETE`
refresh of target observation, and the adapter's AMBIGUOUS reporting after a
crash. Each is bounded and evidence-only.

### Recovery classes

| Failure | Required behavior |
|---|---|
| Transport unavailable (Grok, Telegram) | Missions continue; the fallback surface and break-glass access remain. |
| Operator process disappears (Pi, Codex) | The Reconciler detects STALE and restores the bounded session from durable state, or blocks truthfully. |
| A Herdr role crashes | One Mission's recovery path runs; unrelated Missions continue. |
| Sleep and wake, reboot | Services reload, the registry persists, the Reconciler restores truth. |
| Network loss | State stays durable; external dependencies are marked degraded, never assumed passed. |
| GitHub outage | Checks are marked unavailable; nothing pretends CI passed. |
| Model outage or quota exhaustion | Budget-aware closure or continuation, with evidence and blockers preserved. |
| Stale process after an upgrade | Service identity exposes running versus on-disk commit; a stale process never looks healthy because it is still running. |
| Ambiguous external effect | AMBIGUOUS, reconcile before any retry. |
| Missed UI events | Snapshot plus event catch-up. |

What survives today: the adapter reports queued-but-undispatched work as
dropped and dispatched-but-unconfirmed work as AMBIGUOUS after a restart; the
Runtime claims from the durable store; the LaunchAgents restart an exited
process; a stopped Runtime is an actionable `/status` error naming the remedy
commands. Reboot, sleep and wake, and network-loss validation are open Phase 1
work.

### One Mission failure must not destroy another

Missions are isolated in identity, context, budget, authority, evidence,
workspaces, and worker leases. When one Herdr Pod loses an Executor, only that
Mission enters recovery. When one Mission blocks on a credential, the others
continue.

An overnight run is the shape of the target: three Missions running, bounded
maintenance Missions inspecting flaky tests and dependency alerts without
merge or deploy authority, one Mission losing an Executor and recovering alone,
a network drop that marks GitHub checks unavailable rather than passed, one
Mission going BLOCKED on an expired credential and queueing a human item rather
than broadening its own credentials, and a morning summary that says exactly
which two things need a person. The acceptance test for that is three
simultaneous Missions under injected failure with zero cross-Mission
contamination.

The tree enforces the principle at the record level: a workflow record at a
hard bound stops that one workflow durably with a truthful capacity code
(`broker_record_capacity_exhausted` / `runtime_codex_turn_capacity_exhausted`);
the Runtime process and every other workflow keep running. True per-mission
concurrency does not exist yet; the Telegram adapter still serializes Gateway
turns through one worker.

## 13. Multimodal

Multimodal input is a first-class Mission input, not a preprocessing step in
front of a text system.

```text
                       USER
                         │
       ┌─────────────────┼──────────────────┐
       │        │        │        │         │
       ▼        ▼        ▼        ▼         ▼
     Text     Image    Voice    Video      File
                         │
                         ▼
               Multimodal Intake
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
   Artifact Registry               Understanding /
   original evidence                transcription /
                                    visual analysis
          │                             │
          └──────────────┬──────────────┘
                         ▼
                    Mission Context
```

A screenshot showing a bug, a phone screen recording of Mission Control
freezing, a voice note describing a new product, a PDF contract, an
architecture diagram, logs, a CSV, a source archive, an image of an error, a
video of device behavior: each is Mission input, and the original media stays
attached to the Mission as evidence. No manual re-description of the media
should be required, and the transcription or visual analysis is a derived
artifact beside the original, never a replacement for it.

## 14. Cross-Mission work

Missions are isolated in authority and joined by references. A verified
artifact from one Mission can be consumed by another without being flattened
into a pasted summary:

```text
Mission 201
DBOS Research
        │
        ▼
verified research artifact
        │
        ├───────────────────────────────┐
        ▼                               ▼
Research Bot                       Engineering Bot
                                        │
                                        ▼
                               Mission 202
                               Worker architecture
```

So the question

> Ask Engineering Bot whether Research Bot's DBOS result changes our design.

is answered against the registered artifact and its evidence, by a bot that
can cite it. Explicit parent/child, dependency, follow-up, and supersedes
relationships are first-class, and related Missions never automatically inherit
each other's authority: a research artifact can inform an engineering Mission,
but the engineering Mission still needs its own Mission Authorization.

The same holds for attention. Asked "what needs me?", the Coordinator does not
guess; it queries Dodging Infinity:

```text
MISSION REGISTRY

#201 Worker seam        Engineering  RUNNING (Herdr Reviewer round 2)
#202 DBOS research      Research     VERIFYING
#203 Zenmo launch       Marketing    WAITING FOR HUMAN
#204 SaaS audit         Operations   BLOCKED: login expired
```

and answers that #203 needs content approval, #204 needs credential recovery,
and the other two need nothing.

## 15. Stable core vs replaceable services

The core architectural goal:

> **We should be able to replace a provider, bot, engineering team, durability
> engine, browser engine, or worker without redefining what a Mission is.**

```text
STABLE DI CORE              REPLACEABLE SERVICES
──────────────              ────────────────────

Mission identity            Grok Bot        Herdr
Mission Authorization       Telegram        DBOS
Authority Ledger            Pi              Playwright
Lifecycle state             Codex           GitHub
Evidence                    GPT             Tailscale
Artifacts                   Claude          Workers
Observation                 Grok            Future providers
Reconciliation              Muse
Verification
Human delivery gates
```

The DI-owned components are the Mission Harness, the Mission Router, the
Attention Router, the Bot Coordination Router, the Authority Ledger, the
Mission Observation Service, the Reconciler, `OperatorSession`,
`HumanInteractionAdapter`, `DurableExecution`, `Capability`, `Worker`, the
Capability Broker, and the Artifact and Evidence Registry. Those are the stable
control-plane concepts.

Everything else is a dependency with a stated role, a replaceability, and an
authority owner:

| Service / system | Role | Required for architecture? | Replaceable? | Authority owner |
|---|---|---:|---:|---|
| **Grok Bot / xAI** | Primary conversational bot plane | No | Yes | DI |
| **Claude / Anthropic** | Reasoning + Herdr agents | No | Yes | DI / Herdr scope |
| **OpenAI / GPT** | Reasoning / Operator backend | No | Yes | DI |
| **Muse Spark** | Possible model / engineering executor | No | Yes | DI / Herdr scope |
| **Pi** | Preferred operator runtime adapter concept | No | Yes | DI |
| **Codex** | Reference/fallback operator adapter | No | Yes | DI |
| **Herdr** | Engineering execution capability | Engineering missions | Yes, architecturally | DI |
| **DBOS** | Candidate durable execution substrate | No | Yes | DI |
| **GitHub** | Source / PR / merge / release target | For GitHub missions | Yes in principle | Human via DI |
| **Telegram** | Current remote interaction transport | No | Yes | DI |
| **Tailscale** | Secure remote networking / SSH | No | Yes | Human |
| **SSH** | Break-glass terminal access | No | Yes | Human |
| **Browser / Playwright** | Browser-backed capability | No | Yes | DI |
| **SaaS / APIs** | Mission-specific external systems | No | Yes | DI |
| **GPU / Simulators** | Specialized worker capability | No | Yes | DI |

The dependency principle:

```text
        Dodging Infinity
              │
              ├── may use Grok
              ├── may use Claude
              ├── may use GPT
              ├── may use Muse
              ├── may use Pi
              ├── may use Codex
              ├── may use Herdr
              ├── may use DBOS
              ├── may use GitHub
              └── may use Tailscale

None of those services owns the Mission.
```

Read top to bottom, the same thing is a layering:

```text
LAYER 1 — HUMAN SURFACES        Grok Bot, Telegram, desktop UI, CLI, Tailscale SSH
LAYER 2 — DI CONTROL PLANE      interaction, routing, Mission Harness, authorization,
                                observation, reconciliation, artifacts and evidence
LAYER 3 — REASONING             Pi, Codex, GPT, Claude, Grok, Muse, future models
LAYER 4 — CAPABILITIES          Herdr, research, browser, document/ops, media, publishing
LAYER 5 — WORKERS               trusted Mac, GPU workers, browser workers, simulators
LAYER 6 — EXTERNAL WORLD        GitHub, repositories, web, SaaS, APIs, VPN, devices
```

Two design references are named for honesty rather than dependency: **pstack**
for persistent-browser and skill-pack patterns, and **Munder Difflin** for
visual presentation of a multi-agent organization. Neither is a backend and
neither carries authority.

## 16. Current implementation notes

Most of this document is design. This section is the line between what runs and
what is intended, one row per subsystem. Five words carry the distinction:

| Status | Meaning |
|---|---|
| **Proven** | Exists in this repository and is pinned by a named test, a CHANGELOG entry, a CI run, or a release evidence identifier. |
| **Seam** | Phase 1 work that exists in this checkout as an initial abstraction and does not yet implement its target design. |
| **Target** | Design intent. Nothing in the tree implements it. |
| **Candidate** | A third party under evaluation for a target role. Not selected and not depended on: Pi, DBOS, and PostgreSQL through DBOS. |
| **Reference** | The current implementation of a role the target design makes replaceable: Telegram as transport, Codex as Operator. Neither is permanent architecture. |

Where the evidence is ambiguous, this page takes the narrower claim and says
so.

### Subsystem status

| Subsystem | Status | Pin or location |
|---|---|---|
| DI-REMOTE-2 remote target routing: remote mission authorization, isolated managed target, target Herdr bootstrap, evidence-gated verification, exactly-once final Telegram result, fail-closed ambiguity, separate human Git gates, deterministic Runtime and Broker boundaries, protected workflow authority state | Proven | `tests/test_release_narrative.py`; CHANGELOG v0.7.0; [release evidence](release-evidence-v0.7.0.md) |
| Isolated managed target materialization with containment, canonical-remote, and baseline verification | Proven | `tests/test_target_runtime.py`; `tests/test_workspace_trust.py` |
| Herdr: Supervisor, Lead, Executor, Reviewer; canonical review decisions; human commit, push, and tag gates | Proven | Herdr and Git guard suites; [operations.md](operations.md#8-herdr-operations) |
| Evidence-gated verification: `verified_result` necessary, never sufficient; eight conjuncts, ten independent problem codes | Proven | release narrative suite (gate registry pin) |
| Exactly-once final Telegram result: a bot-owned placeholder bound before dispatch, edited after verification, never a second delivery call | Proven | release narrative suite (mutation-pinned I5) |
| Protected workflow authority state: schema-2 store, atomic, cross-process-locked, fail-closed | Proven | `workflow_authority` suites |
| Read-only observation: `herdctl observe` schema v3, `herdctl health` | Proven | `tests/test_docs_i8.py` |
| Clean-clone CI green on all four macOS/Ubuntu × Python 3.9/3.13 jobs | Proven | GitHub CI run `33330263889` at `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f` |
| Telegram as transport; Codex as Operator behind a Gateway that cannot reach Herdr | Reference | [operations.md](operations.md#9-telegram-remote-control); static isolation suite |
| `OperatorSession` `prepare()` / `execute()` boundary, `PreparedTurn`, `CodexOperatorSession` | Seam | `operator_session/`; `tests/test_operator_session.py` |
| `HumanInteractionAdapter` with `TelegramHumanInteractionAdapter` as the reference implementation | Seam | `human_interaction/contract.py`; `telegram_operator/interaction.py` |
| `DurableExecution` contract (substrate-free; no substrate adapter) | Seam | `durable_execution/contract.py`; `tests/test_durable_execution.py` |
| `Capability` contract (substrate-free one-shot authority boundary) | Seam | `capability/contract.py`; `tests/test_capability.py` |
| Mission Harness, Mission Registry and Manifest, `M-####` identity, Authority Ledger, Evidence Graph, Blocker Ledger, Artifact Registry, budgets, checkpoints, event journal | Target | Sections 2 and 5 |
| Mission Router, Attention Router, Scheduler, multi-mission execution | Target | Section 4 |
| Reconciler and Observation Service | Target | Section 12 |
| Grok Bot plane and the broader interaction surface set | Target | Section 3 |
| Full `OperatorSession` lifecycle, provider selection, Domain Operator Profiles, Skill Packs | Target | Section 6 |
| `Worker` abstraction and Worker Registry; BrowserCapability; Action Risk Envelope; richer artifact delivery | Target | Sections 7 and 9 |
| Telegram-native exact delivery authorization (commit, push, PR, merge, tag, release, deploy receipts) | Target | Section 11 |
| Ops Steward; Visual Mission OS | Target | [roadmap](roadmap.md) |
| Pi as operator runtime; DBOS as durability substrate; PostgreSQL through DBOS | Candidate | Sections 5 and 6 |

### What the live evidence does and does not cover

One historical external-target mountain exercised the production path once. It
reached target Herdr COMPLETE with a canonical target Reviewer APPROVE and then
correctly terminated BLOCKED at `broker_verification_policy_drift`, with
`verified_result` and `result_delivery` null and no target Git delivery. The
corrected verification and final-result path was certified later, hermetically
and adversarially. A fresh post-fix live mountain is not release evidence, and
separate artifact delivery is not certified. The exact record is on the
[release evidence](release-evidence-v0.7.0.md) page, and it is never described
as a successful end-to-end run.

### How to check a claim yourself

1. Find the row. If the status is **Proven**, the pin column names a test file,
   a CHANGELOG entry, or an evidence identifier.
2. Run the pin from the repository root, for example:

   ```bash
   PYTHONPATH="$PWD" python3 tests/test_operator_session.py
   PYTHONPATH="$PWD" python3 tests/test_docs_i8.py
   PYTHONPATH="$PWD" python3 tests/test_release_narrative.py
   ```

3. For a CHANGELOG pin, open [CHANGELOG.md](../CHANGELOG.md) at the v0.7.0
   entry and match the identifier.
4. For a CI pin, open the run id on GitHub Actions and match the commit SHA.
5. If the status is **Seam**, read the module the row names.
6. If the status is **Target** or **Candidate**, expect to find nothing in the
   tree. A grep that finds an implementation means this page is stale.

Code and tests decide what is proven. [CHANGELOG.md](../CHANGELOG.md) is the
release record. [SECURITY.md](../SECURITY.md) states the trust boundary and its
known limits. [OPERATOR_PROTOCOL.md](../OPERATOR_PROTOCOL.md) and
[AGENTS.md](../AGENTS.md) are the operator contracts.

## 17. Glossary

| Term | Definition |
|---|---|
| Action Risk Envelope | Classification of a proposed external action by confidence, blast radius, reversibility, external effect, credential scope, ambiguity risk, and data sensitivity. Changes review, cadence, and escalation; cannot create authority. |
| AMBIGUOUS | The state of a Mission, or of one external effect, when a non-idempotent action may have happened and its outcome is unknown. Nothing is retried until reconciled. |
| Artifact | A Mission-owned file (Markdown, PDF, CSV, XLSX, DOCX, PPTX, image) with a digest, allowed type, size bound, containment check, and delivery state. Artifact delivery is separate from Git authority. |
| Artifact Registry | The durable record of a Mission's artifacts and their review and delivery state. |
| Attention Router | Push: sends the human only the things that need attention. Never steers the Operator or Herdr. |
| Authority | Permission to perform a consequential action. Held by humans; carried by transports; requested by models; displayed by UIs; minted by none of them. |
| Authority Ledger | The durable record of every authorization and its consumption. |
| Blocker Ledger | The durable record of what is stopping a Mission and why. |
| BrowserCapability | A persistent browser behind Dodging Infinity's authority, with read operations, write operations, and human handoff kept distinct. Playwright is the planned dependency. |
| Capability | A specific thing a Mission is allowed to do, brokered against its authorization at the moment of use. |
| Capability Broker | The component that brokers capabilities. The Target Broker (nine fixed lifecycle actions, Runtime-minted one-shot capabilities) is its narrow ancestor. |
| Codex | The current reference implementation of the Operator role, reached through the Codex Gateway, and the fallback until another path is proven. Not the role itself. |
| Codex Gateway | The local, transport-neutral interface boundary in front of the reference Operator. Cannot reach Herdr. |
| DBOS | Candidate durable workflow and queue substrate behind the DurableExecution interface. Not selected. |
| Delivery authority | Permission to commit, push, open a PR, merge, tag, release, or deploy. Always `none` at Mission authorization. |
| DI-REMOTE-2 | Remote Target Repository Routing: one exact bounded Mission against a remote GitHub target from a Telegram request, with this repository as the permanent control and policy repository. Released in v0.7.0. |
| `dirun` | The DI-REMOTE-2 Runtime entry script. A separate deterministic process that claims authorized workflows and advances the lifecycle. |
| Domain Operator Profile | Reusable specialization for a Mission class: Skill Pack, context sources, proof requirements, worker requirements, escalation policy. Improves reasoning; grants no authority. |
| DurableExecution | The interface (start, enqueue, schedule, cancel, resume, checkpoint, inspect, recover) between the Harness and a durability substrate. |
| Evidence Graph | Durable proof of what was tested, reviewed, measured, or verified, per Mission. |
| Executor | The Herdr role that implements. |
| Grok Bot | The target conversational plane with named specialist experiences. Owns no authority. |
| Herdr | The engineering organization inside an engineering Mission: Supervisor, Lead, Executor, Reviewer. Not the Mission control plane. |
| Herdr Pod | The isolated Herdr created for one engineering Mission. |
| `herdctl` | The Herdr command-line tool: setup, presets, rules, missions, tasks, review decisions, human Git gates, health, observation. |
| Human gate | A deterministic one-shot human authorization bound to exact state: the commit, push, and release-tag gates. |
| HumanInteractionAdapter | The provider-neutral human interaction boundary. Telegram is the current reference implementation. |
| Lead | The Herdr role that owns acceptance and validates the Reviewer's decision. |
| Mission | One durable piece of work with its own objective, rules, evidence, budget, status, and authority. The current ancestor is a DI-REMOTE-2 workflow. |
| Mission Authorization | The human approval of an exact Mission before consequential execution. A closed-schema document binding destination and boundaries, approved one-shot on the bound message. |
| Mission Harness | The authoritative core: registry, manifest, authorization, ledgers, lifecycle, evidence, artifacts, budgets, checkpoints, readiness, scheduling, Reconciler, Observation Service, event journal. |
| Mission Manifest | The written contract describing exactly what a Mission may do and what counts as success. |
| Mission Registry | The durable list of Missions and their identity. |
| Mission Router | Inbound identity: which Mission a conversation refers to. Routes identity only; does not engineer. |
| Munder Difflin | Presentation design reference for the visual Mission world. Not the backend. |
| Observation Service | Pull: read-only status from canonical state. Never interrupts or steers. `herdctl observe` is its ancestor for one herd. |
| Operator | The replaceable reasoning role for one Mission. A role, not a model brand. |
| OperatorSession | The provider-neutral boundary around the Operator. Today `prepare()` / `execute()`; the target lifecycle is wider. |
| Ops Steward | The organizational learning layer that finds repeated failures and proposes improvements through a governed path. Cannot expand its own authority. |
| Pi | Preferred candidate provider-neutral operator runtime. Not selected. |
| Playwright | Planned browser automation dependency behind BrowserCapability. |
| PostgreSQL | Potential durable store, only through DBOS. |
| pstack | Source of persistent-browser and skill-pack design ideas. Its authority model is not inherited. |
| Reconciler | Deterministic machinery that compares durable expected state with reality. Not an AI agent. |
| Reviewer | The independent, read-only Herdr role that returns exactly one canonical decision. Its APPROVE is necessary, never sufficient. |
| Runtime | The DI-REMOTE-2 process (`target_runtime/`, `dirun`) coupled to the control chain only through the durable workflow store. |
| Scheduler | Decides which Missions and workers get capacity. Never combined with the Mission Router. |
| Skill Pack | Encodes how to approach a class of Mission. Guides reasoning; authorizes nothing. |
| Supervisor | The first strategy-bearing Herdr role. Owns decomposition, route, sequencing, and the validation workflow. |
| Target Broker | The privileged fixed-action component that validates and consumes Runtime-minted one-shot capabilities. |
| Telegram | The current reference transport for remote intent, approval, status, and results. Carries authority; never mints it. |
| `tgop` | The Telegram adapter entry script. |
| Verified result | A result that passed the evidence gate: a Broker-decided conjunction of eight conjuncts against a fresh disk read, never a model's declaration. |
| Visual Mission OS | The final projection of canonical state, built last. Never the backend. |
| Worker | A capability-aware bounded execution host. The trusted Mac is Worker 1, not a permanent singleton. |
| Worker Registry | The durable list of workers, capabilities, credential classes, capacity, leases, and health. |
| Workflow authority store | The durable `workflow_authority/` store: schema-2, atomic, cross-process-locked, fail-closed. |
