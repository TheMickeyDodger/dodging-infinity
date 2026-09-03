# Roadmap

Back to [Home](Home.md). Labels are defined on the Home page.

## How to read the phases

Two numbering systems describe the same work from different distances.

- This page uses Phase 0 to Phase 11: the long-term model from the end-state design. It says what each phase builds and what it must prove.
- The [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md) uses Phase I to Phase V, Iteration 0 to Iteration 18, and Milestones A to I: the detailed near-term plan, with per-iteration acceptance statements and the record of what the historical external-target mountain proved. It is test-pinned and is not edited here.

Neither supersedes the other. Phases 0 to 2 below correspond roughly to Iterations 0 to 3 and the release gate; the milestones map onto Phases 3 to 11. When they disagree on the fine grain, the detailed roadmap is the nearer view.

The North Star that both serve: leave the trusted Mac unattended for seven days, start multiple independent missions from anywhere, talk naturally about any one of them, observe all of them instantly, receive reviewed results and artifacts, authorize exact reviewed delivery actions from a phone, and recover any individual service failure without being physically present.

## Phase 0: v0.7.0, complete

**[IMPLEMENTED / PROVEN]** The v0.7.0 release and its reference proof. DI-REMOTE-2 acceptance is complete for the release tree: reconciled public documentation, hermetic clean-clone CI (run `33330263889`), the historical mountain preserved as terminal diagnostic evidence, the Runtime stabilization lineage integrated into `main`, final certification on the stable tree (continuation task `20260901-165812-045b0c`, 2,048 tests, `OK (skipped=1)`), and the release tree proven green. Tag publication is governed separately by the human authorization gate.

v0.7.0 is the reference proof for remote mission authorization, remote target materialization, Herdr bootstrap, independent verification, exactly-once result delivery, and human Git gates. The evidence trail is on the [release evidence](../reference/release-evidence-v0.7.0.md) page.

Completed foundations, accumulated through v0.6.1, v0.6.2, v0.6.3, and v0.7.0: the durable Herdr mission boundary; Supervisor to Lead to Executor and Reviewer orchestration; repository isolation; the deterministic review protocol; human commit, push, and tag gates; the Codex Operator contract; the plan-scoped operator protocol; `herdctl health` and `herdctl observe` with a schema-versioned observation model; Codex Gateway v0.1 with live compatibility validation; the Telegram Remote Operator MVP with a numeric allowlist, private-chat enforcement, one-shot fully bound plan approval, resumed sessions, status, and verified-result delivery; the optional per-user LaunchAgent; and DI-REMOTE-2 Remote Target Repository Routing.

## Phase 1: host survival and seams

**[IN PROGRESS]** Host survival and architectural seams, with v0.7.0 behavior preserved. The list: Tailscale and SSH break-glass recovery; reboot and login survival; service identity and readiness; `HumanInteractionAdapter`; `OperatorSession`; `DurableExecution`; `Capability`; `Worker`; a DBOS spike; a Pi spike; a Grok Bot spike. Telegram and Codex stay reference implementations throughout.

What exists in this checkout is the first `OperatorSession` `prepare()` / `execute()` seam, and it is on `main`. It stays IN PROGRESS as the initial two-step abstraction, not the target lifecycle. Everything else in the list is open. See [OperatorSession](OperatorSession.md) and [Current vs End State](Current-vs-End-State.md).

## Phases 2 to 5

**[PLANNED / TARGET]**

| Phase | Builds | Proves |
|---|---|---|
| 2: Mission Harness | Mission Manifest, Mission Registry, `M-####` identity, PREFLIGHT, the Mission Authorization gate, AWAITING_MISSION_AUTHORIZATION and NEEDS_REAUTHORIZATION, the lifecycle state machine, Authority Ledger, Evidence Graph, Blocker Ledger, proof requirements, Artifact Registry, budgets, continuation and checkpoints, event journal, snapshots, readiness graph, Reconciler, Observation Service. | The actual mission operating system exists and canonical state is the truth. |
| 3: Routing, attention, and Grok | The natural-language Mission Router with deterministic ambiguity handling, the Attention Router, instant read-only status queries, the Grok interaction adapter with authorization and result cards and exact approval transport, the Telegram fallback adapter, shared-computer security tests, recovery and parity tests. | Grok becomes the preferred front door only once proven; Telegram remains the fallback. |
| 4: Provider-neutral Operator | The full `OperatorSession` lifecycle, the Codex adapter, the Pi RPC adapter, bounded DI tools for Pi, provider and model selection, Domain Operator Profiles, Skill Packs, generated capability documentation, cross-model evaluations. | Provider replacement does not change mission authority or semantics. |
| 5: True multi-mission execution | Independent execution lanes, bounded admission, the Scheduler, per-mission queues, workspace, artifact, and approval isolation, fair scheduling, P0 to P3 priority, independent Herdr Pods, mission relationships, pause, resume, and abort, expensive-verification deduplication. | Three simultaneous missions with zero cross-contamination. |

## Phases 6 to 9

**[PLANNED / TARGET]**

| Phase | Builds | Proves |
|---|---|---|
| 6: Evidence-native capabilities and delivery | BrowserCapability with read and write classification, stale-reference failure, screenshot and snapshot evidence, console and network evidence, ambiguous side-effect reconciliation, human browser handoff; Artifact Registry delivery and richer file types; the Action Risk Envelope; proof-complete feedback loops; the exact remote delivery ceremony in Grok. | Real user-path evidence and exact remote delivery are first-class. |
| 7: Chaos and worker fabric | Injected failure of Grok, Telegram, the Runtime, the Operator, Herdr, sleep and wake, reboot, network, GitHub, model, quota, stale process, blocked mission, ambiguous browser action, interrupted result, artifact, and Git or release action, missed events; then the Worker Registry, leases, simulator, VPN, and GPU capability matching, retention, archival, compaction. | Multiple missions survive a hostile day without losing identity, authority, observability, progress, results, artifacts, or recovery information. |
| 8: Organizational learning | The Ops Steward, repeated-failure and same-mistake-twice detection, repetition-to-automation, bounded nightly missions, recurring monitoring, the postmortem flow, Skill and Profile proposal flow, control metrics, cost reporting. | Recurring interventions become deterministic machinery through a governed path; the Steward cannot change its own authority. |
| 9: Productization | A deterministic installer and upgrader, migrations, rollback, generalized target onboarding, a safe "go solve this issue" flow, an operational desktop, worker and environment onboarding. | Install once, connect a transport, point at a repository, start engineering, without hand-assembling infrastructure. |

The distribution end state from the earlier README is part of Phase 9: an installer that verifies the host, installs and verifies the Herdr runtime, `herdctl`, `codexgw`, the Operator integration, operator contracts, safety guards, the Telegram adapter, and the Mac background service, then runs a deterministic health check and confirms readiness; repository onboarding reduced to `herdctl init`; a desktop app later, as a client of the same operator boundaries and never a replacement execution path.

## Phases 10 and 11

**[PLANNED / TARGET]**

Phase 10 is the general autonomous work fabric: engineering, research, automation assessment, report generation, analysis, monitoring, planning, browser QA, release preparation, and maintenance formalized as native mission classes sharing identity, routing, observability, evidence, and authority infrastructure, plus end-to-end operations transformation missions. The [Examples](Examples.md) page shows what those classes look like from the human's side.

Phase 11 is the Visual Mission OS. It is last. It is an immersive projection of canonical state over the Harness's APIs and events (a snapshot on startup, a subscription after the last event sequence, catch-up or a fresh snapshot when events were missed), and it never spawns Herdr, approves missions, marks missions complete, creates Git authority, deploys, or rewrites durable state. Acceptance requires every lower layer to be stable first.

**[DESIGN REFERENCE]** Munder Difflin is the presentation source for the visual world. Its ideas about showing a multi-agent organization are reused; its orchestration and authority backend are not.

## The detailed near-term plan

**[IMPLEMENTED / PROVEN]** for the crossed-out items, **[PLANNED / TARGET]** for the rest. The [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md) carries the near-term sequence: Iteration 0 (trusted Mac stabilization and break-glass access), Iteration 1 (durable Mission Registry and Mission Router), Iteration 2 (per-mission asynchronous execution lanes), Iteration 3 (out-of-band observability and mission control), Iteration 4 (Runtime identity, upgrade readiness, safe service lifecycle), Iteration 5 (Telegram exact delivery authority and Git decision surfaces), Iteration 6 (first-class artifact delivery), Iteration 7 (multi-mission mountain and chaos test), and onward through always-on host work, mission fabric maturity, distribution, and the broader work platform.

Its immediate items after the v0.7.0 gate, as the earlier README listed them:

1. Always-on Mac reliability: exercise and harden the shipped LaunchAgent model; validate login startup, restart-on-failure, sleep, wake, reboot, network loss and recovery, protected configuration and state over long runtimes, session continuity and crash-ambiguity reporting, and actionable local health. Engineering execution stays on the trusted Mac.
2. Broaden external-repository validation: repeat the certified DI-REMOTE-2 workflow against unrelated repositories and real issues, then exercise multi-mission and hostile recovery conditions. A fresh post-fix live mountain may provide useful additional production evidence, but it is not used as the v0.7.0 release prerequisite or release proof. Remote delivery remains a separately authorized roadmap stage.
3. Distribution and productization.
4. A desktop app later, as a client of the same boundaries.

## Why the visual world is last

**[PLANNED / TARGET]** A visual world is only worth building over state that is already canonical, durable, observable, and recoverable. Built earlier, it would either invent state the backend does not hold or become a second control path around the authority model, which is the one thing the design forbids. Built last, it is a consequence of real events: Supervisor planning, Executor working, Reviewer rejected, mission blocked, verification running, artifact registered, human approval ready, delivery locked. If it crashes, the missions keep running. The renderer is never the backend.
