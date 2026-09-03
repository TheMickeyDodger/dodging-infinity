# Dodging Infinity wiki

This is the canonical reviewed source for what Dodging Infinity is, what it proves today, and where it is going. The [project README](../../README.md) is the front door; these pages are the depth behind it.

## What Dodging Infinity is

Dodging Infinity is a governed mission fabric for giving AI real work without giving AI uncontrolled authority.

**[IMPLEMENTED / PROVEN]** A person states an objective. In v0.7.0 the system turns it into one bounded remote mission: an exact target and baseline, explicit rules, a one-shot human approval before anything consequential runs, evidence-gated verification before the work can be called finished, and separate local human gates for commit, push, and release tag. The person can start the mission from a phone, ask what is happening without interrupting the work, and receive a verified result. Recovery today is bounded and real: the Runtime claims from a durable store so a restart does not lose an authorized workflow, authority-bearing state is persisted before any external action, an ambiguous placeholder creation fails closed before dispatch, dispatched-but-unconfirmed Gateway work is reported AMBIGUOUS after a crash and never replayed, an ambiguous edit outcome on the bound result message is retried only as an idempotent edit of that same message, and the LaunchAgents restart an exited process. See [runtime and host](../reference/runtime-and-host.md) and [Observation and Recovery](Observation-and-Recovery.md).

**[PLANNED / TARGET]** The target adds PR, merge, release, and deploy gates with exact one-shot remote delivery approvals; a durable `M-####` mission record with budgets and checkpoints; many missions running at once; and a general recovery model, a deterministic Reconciler across transport, Operator, Herdr roles, sleep and wake, reboot, network, GitHub, model, quota, stale process, ambiguity, and missed UI events, with the guarantee that one mission failure cannot destroy another. The mission record, the remote delivery approvals, multi-mission execution, and the general Reconciler have no machinery in the tree.

It is not a wrapper around Telegram, Grok, Codex, or Pi. It is not a persona collection, a multi-agent chat demo, a task board, a generic workflow engine, or an autonomous Git bot. It is not merely Herdr, and it is not a Herdr UI. It is not a visual simulation that controls agents. Each of those can be an interface, an implementation, a dependency, a candidate, or a reference. The product is the governed mission fabric that joins them.

## The operating model

> **Grok Bot converses. Operator operates. Herdr engineers. Dodging Infinity governs. Humans authorize. The Reconciler keeps truth current. The Ops Steward learns. The world observes.**

Each sentence names a role and its limit.

| Role | What it does | What it never does | Status |
|---|---|---|---|
| Grok Bot | The preferred future conversational plane, with specialist experiences for coordination, research, operations, release, and QA. | Does not own authority. | PLANNED / TARGET |
| Operator | The replaceable reasoning role for one mission. Assesses evidence, chooses bounded work, invokes approved capabilities, coordinates engineering when required. | Becomes a model brand. Mints authority. | REFERENCE / FALLBACK today (Codex); PLANNED / TARGET for the provider-neutral role |
| Herdr | The engineering organization inside an engineering mission: Supervisor, Lead, Executor, Reviewer. | Becomes the mission control plane. | IMPLEMENTED / PROVEN |
| Dodging Infinity | The authoritative system. Today (v0.7.0 control chain): mission authorization, workflow identity and lifecycle, rules, authority, evidence-gated verification, blockers, and canonical status. Target (Mission Harness): artifacts, scheduling, budgets, workers, checkpoints, recovery, reconciliation, and delivery receipts. | Delegates authority to a model, a transport, or a UI. | IMPLEMENTED / PROVEN for the v0.7.0 control chain; PLANNED / TARGET for the Mission Harness |
| Humans | The root of consequential authority. | Get replaced by an approval that a machine minted. | IMPLEMENTED / PROVEN |
| Reconciler | Deterministic machinery that compares durable expected state with reality. | Acts as an AI agent. | PLANNED / TARGET |
| Ops Steward | A later organizational learning layer that finds repeated failures and proposes improvements. | Expands its own authority. | PLANNED / TARGET |
| The world | A visual projection of canonical state, deliberately last. | Becomes the backend. | PLANNED / TARGET |

The full stack is on [Architecture](Architecture.md). The line between what exists and what is design is on [Current vs End State](Current-vs-End-State.md).

## How to read these pages

Every major claim on every page carries one of six labels, written exactly as below. A section's first paragraph, or a `Status` column in its table, carries the label for that section. Pages that mix present and target material label every paragraph.

| Label | Meaning |
|---|---|
| IMPLEMENTED / PROVEN | Exists in this repository and is pinned by a named test, a CHANGELOG entry, a CI run, or a release evidence identifier, cited in the same paragraph or table row. |
| IN PROGRESS | Phase 1 work that exists in this checkout and does not yet implement its target design. Today that includes the initial OperatorSession and HumanInteractionAdapter seams. |
| PLANNED / TARGET | Design intent. Nothing in the tree implements it. |
| CANDIDATE | A third party under evaluation for a target role. Not selected and not depended on. Pi, DBOS, and PostgreSQL through DBOS. |
| REFERENCE / FALLBACK | The current implementation of a role that the target design makes replaceable. Telegram as transport and Codex as Operator. Neither is permanent architecture. |
| DESIGN REFERENCE | A project whose ideas are borrowed. pstack for persistent browser patterns, Munder Difflin for visual presentation. Neither is a backend and neither carries authority. |

Where the evidence is ambiguous, the pages take the narrower claim and say so.

## Current state in one table

| Subsystem | Status | Where to look |
|---|---|---|
| DI-REMOTE-2 remote target routing: remote mission authorization, isolated managed target, target Herdr bootstrap, evidence-gated verification, exactly-once final Telegram result, fail-closed ambiguity, separate human Git gates, deterministic Runtime and Broker boundaries, protected workflow authority state | IMPLEMENTED / PROVEN | [README](../../README.md#what-works-today-v070), [release evidence](../reference/release-evidence-v0.7.0.md) |
| Herdr: Supervisor, Lead, Executor, Reviewer, canonical review decisions, human commit, push, and tag gates | IMPLEMENTED / PROVEN | [Herdr](Herdr.md), [Herdr operations](../reference/herdr-operations.md) |
| Read-only observation: `herdctl observe` schema v3, `herdctl health` | IMPLEMENTED / PROVEN | [README](../../README.md#herdctl-observe), [observability](../reference/observability.md) |
| Telegram as transport; Codex as Operator | REFERENCE / FALLBACK | [Telegram reference](../reference/telegram-remote-operator.md), [Codex Gateway](../reference/codex-gateway.md) |
| OperatorSession `prepare()` / `execute()` seam, on `main`, initial abstraction only | IN PROGRESS | [OperatorSession](OperatorSession.md) |
| HumanInteractionAdapter seam with TelegramHumanInteractionAdapter as the current/reference implementation, on `main`, initial abstraction only | IN PROGRESS | [Architecture](Architecture.md), [Telegram reference](../reference/telegram-remote-operator.md) |
| Mission Harness, Mission Registry and Manifest, Authority Ledger, Evidence Graph, Blocker Ledger, Artifact Registry, budgets, checkpoints, Reconciler, Observation Service, Attention Router, Scheduler, multi-mission execution | PLANNED / TARGET | [Missions and Lifecycle](Missions-and-Lifecycle.md), [Observation and Recovery](Observation-and-Recovery.md) |
| Grok Bot plane, broader interaction surfaces, full OperatorSession lifecycle, Domain Operator Profiles, Skill Packs, provider selection | PLANNED / TARGET | [Architecture](Architecture.md), [OperatorSession](OperatorSession.md) |
| Capability and Worker abstractions, Worker Registry, BrowserCapability, Action Risk Envelope, richer artifact delivery | PLANNED / TARGET | [Capabilities and Workers](Capabilities-and-Workers.md) |
| Ops Steward, Visual Mission OS | PLANNED / TARGET | [Roadmap](Roadmap.md) |
| Pi as operator runtime; DBOS as durability substrate; PostgreSQL through DBOS | CANDIDATE | [OperatorSession](OperatorSession.md), [Architecture](Architecture.md) |
| pstack; Munder Difflin | DESIGN REFERENCE | [Capabilities and Workers](Capabilities-and-Workers.md), [Roadmap](Roadmap.md) |

## The pages

| Page | What it answers |
|---|---|
| [Architecture](Architecture.md) | The full target stack, and where this checkout sits inside it. |
| [Current vs End State](Current-vs-End-State.md) | One table per subsystem: what exists, what is in progress, what is target, and how to check a claim yourself. |
| [Authority and Safety](Authority-and-Safety.md) | Intent is not authority. The separate chain. What a transport, a model, and a UI cannot do. Ambiguous external effects. |
| [Missions and Lifecycle](Missions-and-Lifecycle.md) | The durable mission record, the mainline and side states, and what ends a mission. |
| [OperatorSession](OperatorSession.md) | The provider-neutral reasoning seam: what is built and what is designed. |
| [Herdr](Herdr.md) | The engineering organization inside an engineering mission, and what a Reviewer APPROVE does and does not mean. |
| [Evidence and Verification](Evidence-and-Verification.md) | Proof contracts, and why a verified result is gated rather than declared. |
| [Observation and Recovery](Observation-and-Recovery.md) | Reading state without steering it, and surviving failure. |
| [Capabilities and Workers](Capabilities-and-Workers.md) | Bounded execution hosts and the capabilities they carry. |
| [Roadmap](Roadmap.md) | Phase 0 to Phase 11, and the relationship to the detailed near-term roadmap. |
| [Examples](Examples.md) | Five end-state scenarios, every one labelled as target. |
| [Glossary](Glossary.md) | One definition per term, each labelled. |

Operational detail that ships today is in the [reference set](../reference/README.md). The detailed near-term plan is the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md).

## Where the source of truth lives

- Code and tests in this repository decide what is IMPLEMENTED / PROVEN. When a page and a test disagree, the test is right and the page is stale.
- [CHANGELOG.md](../../CHANGELOG.md) is the release record. [SECURITY.md](../../SECURITY.md) states the trust boundary and its known limits. [OPERATOR_PROTOCOL.md](../../OPERATOR_PROTOCOL.md) and [AGENTS.md](../../AGENTS.md) are the operator contracts.
- Two initial Phase 1 provider-neutral seams are on `main`: `OperatorSession` and `HumanInteractionAdapter`. Both stay IN PROGRESS because they are initial abstractions rather than the complete target lifecycles. v0.7.0 at `44bab0b` remains an ancestor of `main`.
- The historical external-target mountain is terminal evidence, stated exactly on the [release evidence](../reference/release-evidence-v0.7.0.md) page. It is never described here as a successful end-to-end run.
