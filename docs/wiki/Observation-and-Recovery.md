# Observation and Recovery

Back to [Home](Home.md). Labels are defined on the Home page.

## Canonical state is the truth

**[PLANNED / TARGET]** Canonical state, not conversation, not a model process, and not a UI, is the durable truth. If Grok, Pi, Codex, Herdr, a desktop window, or a worker disappears, the Mission Harness is still the source of truth, and every surface reconstructs from it. A status answer comes from canonical state. A visual world reconstructs from the registry and the event journal. A missed UI event is repaired by a snapshot and an event catch-up, never by asking an agent what happened.

**[IMPLEMENTED / PROVEN]** The v0.7.0 tree already holds to this for what it has. The durable `workflow_authority/` store is the truth for a DI-REMOTE-2 workflow; `/status` reads it while the mission continues independently, without waiting for the mission's Operator turn. Adapter authority-bearing state is persisted before any external action. Task state, review files, and the observation projection are on disk under `.herd/`. The historical mountain proved the read path in production: `/status` reported Runtime state, workflow phase, target, and target Herdr task from durable state while the mission ran.

## Observation is pull-based and read-only

**[IMPLEMENTED / PROVEN]** `herdctl observe` builds a strictly read-only point-in-time projection of a repository's Herdr, schema version 3, with a `completeness` field that describes visibility only. It does not mutate, repair, prompt agents, change workflow, or control execution. Observation is a reporting surface, not a gate. Bounds are constants, disclosed rather than silently presenting partial information as complete. `herdctl health` is the read-only readiness probe beside it. The schema contract is in the [README](../../README.md#herdctl-observe) and is pinned by `tests/test_docs_i8.py`; the commands are on the [observability](../reference/observability.md) page.

**[PLANNED / TARGET]** The Observation Service generalizes that to missions. A human asks "what is happening with M-1842?", "what did Reviewer say?", "what changed in the last twenty minutes?", "show me everything blocked", "what is waiting on me?". Those queries are read-only. They do not stop the Operator, steer the Operator, interrupt Herdr, consume engineering context to answer status, or invent progress percentages. The answer is elapsed time, Operator health and last activity, Herdr role states and review round, current work, evidence status per proof item, worker, budget, blockers, and last meaningful progress, all from canonical state.

## The Attention Router pushes

**[PLANNED / TARGET]** Observation handles pull. The Attention Router handles push: mission authorization ready, clarification needed, mission blocked, Reviewer rejected, verification failed, commit ready, push ready, merge ready, deployment ready, credential action required, ambiguous effect needing a human decision. The human should not have to poll to discover something needs attention. The router sends only what needs a human; it never steers the Operator or Herdr.

**[IMPLEMENTED / PROVEN]** Today the only unsolicited message class is a restart or recovery notice about work the user sent that was interrupted. There is no proactive progress streaming; `/status` is sent again for a fresh snapshot.

## What the observation surface cannot tell you

**[IMPLEMENTED / PROVEN]** These are limits of the evidence, not gaps in the implementation, and each is pinned by a named test in `tests/test_docs_i8.py`.

- The model a RUNNING agent uses is not observable through the agent interface. The projection reports `configured_model`, the model a role's configuration asks for, and states that limit in its own diagnostics. It carries no running-model field and no verdict about a running model, because such a field would imply a distinction the evidence cannot support.
- A verdict cannot distinguish a model substitution from a restart where the substitution preserves the agent's session. The surface says so rather than guessing.
- A turn record written by a different build of the observer is a claim made by different logic. Skew is reported, naming both builds, rather than reconciled silently.
- A role with no turn recorded is omitted from the listing rather than rendered as healthy.
- An agents-unprobed global PARTIAL is expected in production; completeness describes visibility, not health.

The exact wording of these limits is on the [observability](../reference/observability.md#what-observation-does-not-tell-you) reference page.

## The Reconciler

**[PLANNED / TARGET]** The Reconciler is deterministic operational machinery, not an AI agent. It continuously compares what the Harness expects to be true with what is actually happening: Operator health, Herdr task state, Reviewer state, CI and check state, target drift, worker state, browser effects, artifact state, result delivery, external delivery receipts, human approvals, budget, blockers. Its outcomes are closed:

```text
Operator expected ACTIVE + process missing   = STALE, restore
Herdr COMPLETE + no Reviewer decision        = not complete
CI red                                       = preserve failure, diagnose
target changed                               = BLOCKED, reauthorize
browser effect uncertain                     = AMBIGUOUS, reconcile
human approval pending                       = Attention Router
```

**[IMPLEMENTED / PROVEN]** The v0.7.0 tree has deterministic reconciliation for specific cases, not a general Reconciler: dispatch identity recovery under ruling R-3, crash-after-edit recovery against the bound result placeholder, the stale `ACTIVE` to `COMPLETE` refresh of target observation, and the adapter's AMBIGUOUS reporting after a crash. Each is bounded and evidence-only.

## Recovery classes

**[PLANNED / TARGET]** Required behavior per failure class:

| Failure | Required behavior |
|---|---|
| Transport unavailable (Grok, Telegram) | Missions continue; the fallback surface and break-glass access remain. |
| Operator process disappears (Pi, Codex) | The Reconciler detects STALE and restores the bounded session from durable state, or blocks truthfully. |
| A Herdr role crashes | One mission's recovery path runs; unrelated missions continue. |
| Sleep and wake, reboot | Services reload, the registry persists, the Reconciler restores truth. |
| Network loss | State stays durable; external dependencies are marked degraded, never assumed passed. |
| GitHub outage | Checks are marked unavailable; nothing pretends CI passed. |
| Model outage or quota exhaustion | Budget-aware closure or continuation, with evidence and blockers preserved. |
| Stale process after an upgrade | Service identity exposes running versus on-disk commit; a stale process never looks healthy because it is still running. |
| Ambiguous external effect | AMBIGUOUS, reconcile before any retry. |
| Missed UI events | Snapshot plus event catch-up. |

**[IMPLEMENTED / PROVEN]** What survives today: the adapter reports queued-but-undispatched work as dropped and dispatched-but-unconfirmed work as AMBIGUOUS after a restart; the Runtime claims from the durable store; the LaunchAgents restart an exited process; a stopped Runtime is an actionable `/status` error naming the remedy commands. Reboot, sleep and wake, and network-loss validation are open Phase 1 work; see the [runtime and host](../reference/runtime-and-host.md) page and the [Roadmap](Roadmap.md).

## One mission failure must not destroy another

**[PLANNED / TARGET]** Missions are isolated in identity, context, budget, authority, evidence, workspaces, and worker leases. When one Herdr Pod loses an Executor, only that mission enters recovery. When one mission blocks on a credential, the others continue. The acceptance test for Phase 5 and Phase 7 is three simultaneous missions under injected failure with zero cross-mission contamination.

**[IMPLEMENTED / PROVEN]** The v0.7.0 tree enforces the principle at the record level: a workflow record at a hard bound stops that ONE workflow durably with a truthful capacity code (`broker_record_capacity_exhausted` / `runtime_codex_turn_capacity_exhausted`); the Runtime process and every other workflow keep running. True per-mission concurrency does not exist yet; the Telegram adapter still serializes Gateway turns through one worker, and Iteration 2 of the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md) is the near-term step.
