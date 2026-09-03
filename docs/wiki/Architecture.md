# Architecture

Back to [Home](Home.md). Labels are defined on the Home page.

This page describes the full target stack and marks where the v0.7.0 checkout sits inside it. Read [Current vs End State](Current-vs-End-State.md) for the subsystem-by-subsystem table; read this page for how the pieces fit.

## The shape of the system

**[PLANNED / TARGET]** The target stack, top to bottom. Every box is labelled in the sections that follow.

```text
                          HUMAN
                            |
     +----------------------+----------------------+
     |                      |                      |
 Grok Bot plane       Operational UI          Visual world
 (specialist Bots)    (desktop / CLI)         (projection only)
     |                      |                      |
     +----------------------+----------------------+
                            |
               HumanInteractionAdapters
                            |
             +--------------+--------------+
             |                             |
       Mission Router               Attention Router
       (inbound identity)           (outbound attention)
             |                             |
             +--------------+--------------+
                            |
                  DODGING INFINITY MISSION HARNESS
       Mission Registry, Mission Manifest, Mission Authorization,
       Authority Ledger, lifecycle, Evidence Graph, Blocker Ledger,
       Artifact Registry, Action Risk Envelope, budgets, checkpoints,
       readiness graph, Scheduler, Reconciler, Observation Service,
       event journal and snapshots
             |                             |
       Ops Steward                   Durable Execution
       (governed proposals)          (DBOS candidate; replaceable)
                                           |
                                    OperatorSession
                                           |
                                Domain Operator Profile
                                           |
                                      Skill Pack
                                           |
                              +------------+------------+
                              |                         |
                         Pi adapter               Codex adapter
                         (candidate)              (reference / fallback)
                              |
                      GPT / Claude / Grok / later providers
                                           |
                                  DI Capability Broker
             +-----------------------------+-----------------------------+
             |                             |                             |
     BrowserCapability         research / doc / ops tools      engineering handoff
     (read, write, handoff)                                        Herdr Pod
                                                          Supervisor -> Lead -> Executor / Reviewer
             +-----------------------------+-----------------------------+
                                           |
                             capability-aware Worker Registry
                             (the trusted Mac is Worker 1)
                                           |
                       GitHub, web and SaaS APIs, simulators, VPN, GPU, devices
```

**[IMPLEMENTED / PROVEN]** What v0.7.0 implements of that picture is the control chain from a Telegram request to a verified result: Telegram adapter, Codex Gateway, fresh restricted Codex turns, the DI-REMOTE-2 Runtime and Target Broker, the isolated managed target, the target Herdr, evidence-gated verification, and the exactly-once result placeholder. The [README](../../README.md#current-architecture-on-main-remote-target-routing-di-remote-2-v070) holds the principal flow diagram and each component's contract. That is the released v0.7.0 baseline. Phase 1 seams added afterward are called out below; the rest of this page is design.

**[IN PROGRESS]** Two initial seams from the target picture now exist on `main`. `OperatorSession` provides the provider-neutral `prepare()` / `execute()` boundary around the current Codex path. `HumanInteractionAdapter` provides the provider-neutral human interaction boundary, with `TelegramHumanInteractionAdapter` as the current reference implementation. Both remain IN PROGRESS because they are initial abstractions rather than the complete target lifecycles. See [OperatorSession](OperatorSession.md) and the human interaction section below.

## Human interaction surfaces

**[IN PROGRESS]** The initial `HumanInteractionAdapter` seam is now on `main`. The production Telegram controller routes transport operations through `TelegramHumanInteractionAdapter`. Durable cursor and queue state, approval validation, Mission Authorization, result-delivery state, `OperatorSession`, Runtime, Herdr, and Git authority remain outside the interaction seam.

**[PLANNED / TARGET]** The broader target puts every human surface behind this boundary. Grok Bot is the preferred target conversational plane, with named specialist experiences for coordination, research, operations, release, browser QA, and incident recovery. An operational desktop and CLI serve setup, health, and administration. The visual world is a projection of canonical state and is built last.

**[REFERENCE / FALLBACK]** Telegram is the current transport. It stays the reference and the fallback until the Grok adapter is proven, and it never becomes permanent architecture. What it does today is on the [Telegram reference](../reference/telegram-remote-operator.md) page.

A surface can carry an exact human authorization. It cannot mint one. That rule is the subject of [Authority and Safety](Authority-and-Safety.md).

## Mission Router and Attention Router

**[PLANNED / TARGET]** Two routers, two directions, never combined.

The Mission Router answers "which mission is this?" for inbound conversation. It prefers deterministic evidence: an explicit mission id, a reply-to binding, an approval or result binding, an exact repository or issue reference, a known project, a durable alias, a unique contextual match, and only then a bounded fresh routing turn. Its outcomes are closed: an existing mission, a new mission, or a clarification request. If a follow-up could refer to several missions, it asks rather than guesses. It routes identity only; it does not engineer.

The Attention Router answers "what needs a human?" for outbound attention: authorization ready, clarification needed, mission blocked, Reviewer rejected, verification failed, delivery ready, ambiguous effect needing a decision, credential action required. The human should not have to poll to discover something needs attention.

**[IMPLEMENTED / PROVEN]** Today, durable `wf-*` workflow identity, Telegram message binding, target identity, and task identity exist and survive independently of the Gateway turn; the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md) records that under Iteration 1. The first-class `M-####` registry and the natural-language router remain open.

## The Mission Harness

**[PLANNED / TARGET]** The Mission Harness is the authoritative system. It owns mission identity, the Mission Manifest and its Authorization, the Authority Ledger, the lifecycle state machine, the Evidence Graph and proof requirements, the Blocker Ledger, the Artifact Registry and delivery receipts, the Action Risk Envelope, budgets and closure policy, checkpoints and recovery state, the readiness dependency graph, the Scheduler, the deterministic Reconciler, the Observation Service, and a sequenced event journal with snapshots. If Grok, Pi, Codex, Herdr, a desktop window, or a worker disappears, the Harness is still the source of truth. Detail is on [Missions and Lifecycle](Missions-and-Lifecycle.md), [Evidence and Verification](Evidence-and-Verification.md), and [Observation and Recovery](Observation-and-Recovery.md).

**[IMPLEMENTED / PROVEN]** The v0.7.0 tree has a narrower ancestor of this: the durable `workflow_authority/` store (schema-2, atomic, cross-process-locked, fail-closed), the DI-REMOTE-2 workflow lifecycle PLANNED through COMPLETED with BLOCKED and NEEDS_REAUTHORIZATION, the closed-schema Mission Authorization, and a Runtime that advances the lifecycle through Broker-validated one-shot capabilities. It is pinned by `tests/test_target_runtime.py` and the release narrative suite.

## Durable Execution and OperatorSession

**[PLANNED / TARGET]** Dodging Infinity should not rebuild low-level durable workflow machinery unless it has to. A `DurableExecution` interface (start, enqueue, schedule, cancel, resume, checkpoint, inspect, recover) sits between the Harness and whatever substrate provides persistence, queues, retries, and recovery. The substrate is infrastructure. The Harness still owns mission meaning, authority, evidence, routing, and completion rules.

**[CANDIDATE]** DBOS is the first candidate substrate, and PostgreSQL is a potential dependency through DBOS. Neither is selected and neither is depended on.

**[PLANNED / TARGET]** The `OperatorSession` is the provider-neutral boundary around the Operator role. Its target lifecycle is `create`, `prompt`, `steer`, `follow_up`, `abort`, `status`, `events`, `restore`, and `close`. A Domain Operator Profile and a Skill Pack specialize a session for a domain without granting it authority. Pi is the candidate runtime; Codex is the reference and fallback. Potential providers include GPT, Claude, Grok, and future supported providers. The [OperatorSession](OperatorSession.md) page separates what is built from what is designed.

## Capability Broker and capabilities

**[PLANNED / TARGET]** Every consequential thing a mission can do is a Capability, brokered by Dodging Infinity against the mission's authorization. BrowserCapability splits read operations from write operations and hands off to a human for MFA, CAPTCHA, or takeover. Research, document, and operations tools are bounded mission tools. Engineering is a capability that hands off to Herdr. Credential availability on a worker never grants a mission permission to use it. See [Capabilities and Workers](Capabilities-and-Workers.md).

**[IMPLEMENTED / PROVEN]** The v0.7.0 Target Broker is the narrow ancestor: privileged and fixed-action, nine fixed lifecycle actions, each performed against a Runtime-minted one-shot capability bound to exactly one `(workflow_id, action, revision)` tuple, with sensitive values resolved from the protected workflow record and never supplied by the caller. [Capabilities and Workers](Capabilities-and-Workers.md) states that contract and the release narrative suite pins it against the code.

## Engineering handoff to Herdr

**[IMPLEMENTED / PROVEN]** When a mission requires software engineering, the handoff creates an isolated Herdr with Supervisor, Lead, Executor, and Reviewer. The Supervisor is the first strategy-bearing component: the Mission Authorization binds destination and boundaries, never implementation strategy. The Reviewer is independent and read-only, and its canonical APPROVE or REJECT is persisted by the deterministic harness. In v0.7.0 this runs inside the isolated managed target after a one-shot approval, unattended. Herdr is the engineering organization inside an engineering mission. It is not the mission control plane. See [Herdr](Herdr.md).

**[PLANNED / TARGET]** Multiple missions each receive their own Herdr Pod. There is no global engineering Supervisor; Dodging Infinity coordinates the missions and each mission's Supervisor coordinates engineering inside it.

## Workers and external systems

**[PLANNED / TARGET]** A Worker is a capability-aware bounded execution host with declared capabilities, credential classes, capacity, and leases. The trusted Mac is Worker 1, not a permanent singleton. Later workers may be Linux GPU hosts, VPN-connected hosts, browser-only hosts, or simulator hosts. External systems (GitHub, web and SaaS APIs, simulators, devices) are reached only through capabilities on a worker.

**[IMPLEMENTED / PROVEN]** Today, every component from the adapter onward runs on the trusted Mac. The Mac owns the repositories, Git and Codex credentials, the Gateway, the Herdr runtime and agents, local test environments, repository-scoped `.herd` state, and the commit and push gates. The [runtime and host](../reference/runtime-and-host.md) page describes what that host must do and what survives a restart today.

**[IMPLEMENTED / PROVEN]** GitHub is the current source control and release platform, and GitHub Actions is the CI system; the v0.7.0 clean-clone CI evidence is run `33330263889`.
