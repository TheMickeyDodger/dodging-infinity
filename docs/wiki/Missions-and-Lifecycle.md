# Missions and Lifecycle

Back to [Home](Home.md). Labels are defined on the Home page.

## What a mission is

**[PLANNED / TARGET]** A mission is one durable piece of work with its own identity, objective, rules, evidence, budget, status, and authority. It is the unit the whole fabric is built around: routing finds it, the human authorizes it, the Operator reasons about it, Herdr may engineer inside it, the Reconciler keeps its state honest, and delivery happens only through its receipts. One mission never inherits another mission's authority, state, artifacts, approvals, or context.

**[IMPLEMENTED / PROVEN]** The v0.7.0 ancestor of a mission is a DI-REMOTE-2 workflow: a durable `wf-*` record in the `workflow_authority/` store that carries the Mission Authorization, the exact human intent, the target identity and baseline, the bound Telegram placeholder, the target Herdr task id, and the lifecycle phase. The local-mission path uses the Herdr mission contract (`.herd/state/mission.json`) with objective, constraints, rules, acceptance criteria, and verification, documented on the [Herdr operations](../reference/herdr-operations.md) page.

## The durable Mission record

**[PLANNED / TARGET]** The target Mission Manifest is the contract the human approves. Its fields:

| Field | Meaning |
|---|---|
| identity | A durable `M-####` id, title, mission type, and priority. |
| objective | What done means, in the human's terms. |
| target and baseline | The exact repository, environment, or subject, and the exact revision the mission starts from. |
| allowed and forbidden actions | What the mission may do (inspect, edit an isolated worktree, run tests, benchmark, browser QA, dispatch Herdr) and what it may not (commit, push, PR, merge, tag, release, deploy, change credentials). |
| proof requirements | The evidence that must exist before the mission can be verified. See [Evidence and Verification](Evidence-and-Verification.md). |
| runtime and profile | The Operator runtime, provider, Domain Operator Profile, and Skill Pack. |
| worker requirements | Repository access, browser, simulator, VPN, GPU, or device needs. |
| budgets | Wall clock, model, worker capacity, external resources. |
| stop conditions | What triggers reauthorization, block, or closure: material scope change, credential change, ambiguous external effect. |
| delivery authority | `none`, always, at authorization time. |
| evidence, blockers, artifacts, checkpoints | Ledgers the mission accumulates while it runs. |
| lifecycle | The state below, plus the sequenced event journal. |

The human approves an exact revision. A changed manifest is a new revision and needs a new approval.

## Mainline lifecycle

**[PLANNED / TARGET]** The target mainline:

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

PREFLIGHT checks readiness before the human is asked. AWAITING_MISSION_AUTHORIZATION is where the mission waits for the human card. READY means the worker, runtime, and capabilities are leased. CLOSING is budget-aware: stop accepting new work increments, finish the current bounded writer when safe, preserve evidence and blockers, snapshot the workspace, create a continuation manifest, settle roles, and end truthfully. VERIFIED is reached only when the declared proof contract is met and the Reconciler agrees. COMPLETED follows delivery decisions, which are separately authorized.

**[IMPLEMENTED / PROVEN]** The v0.7.0 DI-REMOTE-2 workflow lifecycle, advanced by the Runtime with a Broker-validated one-shot capability at every forward transition:

```text
PLANNED -> AUTHORIZED -> WORKSPACE_READY -> PREPARED -> VALIDATED
        -> DISPATCHED -> VERIFIED -> COMPLETED
```

with BLOCKED and NEEDS_REAUTHORIZATION as side states. The Herdr task lifecycle underneath is `IDLE -> ACTIVE -> COMPLETE`, or `ABORTED`, or `ERROR`. Pinned by `tests/test_target_runtime.py` and the release narrative suite.

## Side states

**[PLANNED / TARGET]**

| State | Entered when |
|---|---|
| REJECTED | The human rejects the proposal. |
| NEEDS_REAUTHORIZATION | A material rule change, or in v0.7.0 terms, exceeding the corrective follow-up bound. |
| BLOCKED | A running mission cannot proceed truthfully: policy drift, a missing dependency, a credential problem, a Reviewer loop that will not converge inside scope. |
| PAUSED, RESUMING | Human or scheduler pause where the semantics permit, and the controlled return to RUNNING. |
| ABORTING, ABORTED | A non-terminal mission ended deliberately, with evidence preserved. |
| AMBIGUOUS | A non-idempotent external effect may have happened and its outcome is unknown. Nothing is retried until the Reconciler establishes reality. |
| STALE | The Harness expects a process or a lease to exist and it does not. |

**[IMPLEMENTED / PROVEN]** In v0.7.0, BLOCKED and NEEDS_REAUTHORIZATION exist on the workflow record. BLOCKED is durable and truthful: the historical external-target mountain stopped BLOCKED at `broker_verification_policy_drift`, a full record stops one workflow with a truthful capacity code, and an identity-unresolved dispatch that cannot bind exactly one provable child stops BLOCKED under ruling R-3. Corrective follow-ups are an authorization-scope bound of 2, not a review-round limit; exceeding it transitions durably to NEEDS_REAUTHORIZATION. Ambiguous placeholder and edit outcomes exist as durable adapter states (`indefinite`, `edit_indefinite`) rather than as a mission state.

## What ends a mission

**[IMPLEMENTED / PROVEN]** AI self-report never completes a mission. In v0.7.0, Herdr lifecycle COMPLETE alone can never verify; the fresh verification turn's `verified_result` is necessary and never sufficient; and VERIFIED is a Broker-decided conjunction of eight conjuncts applied against a fresh disk read, the canonical target Reviewer APPROVE among them as target-produced evidence that the target's own review process ran and concluded, never as independent verification. A workflow dispatched before the protected-surface receipt existed fails closed at verification. The full gate is in the [README](../../README.md#remote-target-repository-routing-di-remote-2-v070).

**[PLANNED / TARGET]** The target generalizes that rule: a mission is VERIFIED when every proof requirement declared at authorization has evidence in the Evidence Graph and the Reconciler confirms that reality matches the durable record. No state is inferred from "the agent said it finished." Herdr never hands the human a commit directly; everything comes back through canonical state first.

## Authorization and reauthorization

**[IMPLEMENTED / PROVEN]** Approval is one-shot and bound to the exact rendered mission text; a v2 approval dispatches no Operator turn, and the Runtime, a separate process, claims the durably consumed authorization on its own. A revised plan invalidates every prior approval in the thread. Replays, mismatches, expiry, and duplicates fail closed. A DI-REMOTE-1 approval can never authorize a v2 target.

**[PLANNED / TARGET]** Reauthorization is a first-class state, not an error. When a mission needs broader rules than it was granted, it stops, the Attention Router tells the human, and the human approves a new manifest revision or rejects. Related missions (parent and child, dependency, follow-up, supersedes) never automatically inherit each other's authority. A browser QA mission that discovers a bug proposes an engineering child mission, and that child needs its own Mission Authorization. See [Examples](Examples.md).

## What exists today

| Piece | Status |
|---|---|
| Durable `wf-*` workflow identity, Telegram binding, target identity, task identity surviving independently of the Gateway turn | IMPLEMENTED / PROVEN |
| Closed-schema Mission Authorization with `delivery_authority: none` and the exact human request | IMPLEMENTED / PROVEN |
| One-shot bound approval; durable consumption; Runtime claim without a Gateway turn | IMPLEMENTED / PROVEN |
| DI-REMOTE-2 workflow lifecycle with BLOCKED and NEEDS_REAUTHORIZATION | IMPLEMENTED / PROVEN |
| `/status` reading durable workflow state while the mission continues independently | IMPLEMENTED / PROVEN |
| `M-####` registry, Mission Manifest, PREFLIGHT, AWAITING_MISSION_AUTHORIZATION, PAUSED, ABORTED, AMBIGUOUS, STALE, budgets, checkpoints, continuation manifests, event journal, mission relationships, Scheduler, multi-mission lanes | PLANNED / TARGET |

The near-term steps toward the target model are Iterations 1, 2, 11, and 12 of the [Remote Mission Fabric roadmap](../remote-mission-fabric-roadmap.md) and Phases 2 and 5 of the [Roadmap](Roadmap.md).
