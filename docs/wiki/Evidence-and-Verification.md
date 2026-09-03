# Evidence and Verification

Back to [Home](Home.md). Labels are defined on the Home page.

## Evidence is declared before execution

**[PLANNED / TARGET]** A mission declares the proof it must produce before it is authorized. The proof requirements are part of the Mission Manifest the human approves, so "done" is defined by the human and the contract, not discovered afterward by the model. A mission that cannot meet its declared proof does not become VERIFIED; it ends truthfully as BLOCKED, or it returns for reauthorization with a narrower or different contract.

**[IMPLEMENTED / PROVEN]** The v0.7.0 ancestor is narrower and already enforces the principle: a mission contract carries explicit acceptance criteria and a verification command into Herdr; a DI-REMOTE-2 Mission Authorization binds acceptance and execution scope before approval; and dispatch stamps a protected-surface receipt so that a workflow dispatched before the receipt existed fails closed at verification. Evidence declared late is not evidence.

## Proof contracts for code, UI, and performance

**[PLANNED / TARGET]** Proof contracts by mission class:

| Class | Required evidence |
|---|---|
| Code | The diff; regression tests; focused tests; adversarial checks; Reviewer decision; authoritative verification against the full suite. |
| UI | Before and after screenshots; an interaction trace; console health; network health; Reviewer decision. |
| Performance | A baseline measurement; a candidate measurement; a stated tolerance; Reviewer decision. |
| Artifact | An exact digest; an allowed type; a size bound; containment; Reviewer decision. |

Each item becomes a node in the Evidence Graph with its producer, its inputs, and its digest. A Reviewer decision is one node, not the graph.

**[IMPLEMENTED / PROVEN]** Today the code contract is the one that runs: Herdr verification commands, focused regression, the full suite, and a canonical Reviewer decision, with the results recorded in the task state and the review files under `.herd/state/reviews/`. The v0.7.0 release record is an instance: focused regression 159/159, `tests/test_target_runtime.py` 250/250, static checks, a Python 3.9.6 compile pass, `git diff --check`, and the authoritative unchanged-tree discovery of 2,048 tests. UI and performance contracts have no machinery in the tree.

## Verification is not self-report

**[IMPLEMENTED / PROVEN]** AI self-report never completes a mission. In v0.7.0:

- A free-form model message is never reinterpreted as an approved plan or a verified result; the Operator answers through a versioned envelope (plan, status, result, error).
- Herdr lifecycle COMPLETE alone can never verify a workflow.
- The fresh verification turn is a separate read-only process that consumes a bounded, streamed, read-only evidence projection rendered into its prompt. Its `verified_result` is NECESSARY, NEVER SUFFICIENT.
- VERIFIED is a Broker-decided conjunction: eight conjuncts (ten independent problem codes) applied against a fresh disk read.
- Observation completeness is SOURCE-SCOPED (ruling R-6): a decision is blocked only by a demoting diagnostic in its registered consumed-source set; the raw global completeness is recorded and rendered unaltered; an agents-unprobed global PARTIAL is expected in production.

Pinned by the release narrative suite, which checks the conjunct and problem-code counts against the gate registry in the code.

## Reviewer APPROVE is necessary, not sufficient

**[IMPLEMENTED / PROVEN]** The canonical target Reviewer APPROVE is one of the eight conjuncts. It is TARGET-PRODUCED evidence that the target's own review process ran and concluded; it is never independent verification. The historical external-target mountain is the demonstration: target Herdr task `20260830-094026-9fef2d` reached COMPLETE and a canonical target Reviewer APPROVE was recorded, and workflow `wf-2c901885473fc4781bf82296` still exposed genuine post-dispatch policy drift and correctly terminated BLOCKED at `broker_verification_policy_drift`. `verified_result` and `result_delivery` stayed null, and no target Git delivery occurred. A gate that took APPROVE as sufficient would have delivered.

**[PLANNED / TARGET]** The target keeps that shape and adds the Reconciler: a mission is VERIFIED when the Evidence Graph satisfies the declared contract and the Reconciler confirms that reality (the target repository, CI, the worker, the artifacts) matches the durable record. Reviewer APPROVE matters. It is never, by itself, authoritative mission verification.

## The v0.7.0 verification gates

**[IMPLEMENTED / PROVEN]** The gates that stand between a target Herdr COMPLETE and a Telegram result, in order:

1. Dispatch identity. An identity-unresolved dispatched workflow runs one fresh `status_recovery` turn and the evidence-only `reconcile_dispatch` action, which binds EXACTLY ONE provable existing child (exact leased-workspace realpath plus the lease's own observed task id) or stops durably BLOCKED. Under ruling R-3 it reads NOTHING outside this repository, the derived alias is never binding evidence, and more BLOCKED outcomes are the accepted cost.
2. Observation. The Runtime observes the target through the read-only Herdr observability; the herd's own stopped set drives the stopped decision under source-scoped completeness, and a projection degraded in a consumed source WAITS.
3. Verification. The fresh verification turn, the eight-conjunct gate, and the protected-surface receipt check.
4. Delivery. The verified result edits the bot-owned placeholder bound before dispatch, exactly once. Terminal delivery states are disclosed rather than retried.

Failed or ambiguous states fail closed durably and surface through `/status` with concrete remedies. A record at a hard bound stops that one workflow with a truthful capacity code and never kills the Runtime. The exact contract, with its identifiers, is in the [README](../../README.md#remote-target-repository-routing-di-remote-2-v070); the evidence trail is on the [release evidence](../reference/release-evidence-v0.7.0.md) page.

## The Evidence Graph

**[PLANNED / TARGET]** The Evidence Graph is the durable record of what was tested, reviewed, measured, or verified, per mission: nodes for each proof item, edges to the artifacts, commands, and roles that produced them, digests throughout. It is what the Observation Service reads to answer "what did Reviewer say?" and "what is still pending?" without asking the Operator. It is what the Reconciler compares against reality. It is what the delivery ceremony shows the human before an exact commit is approved. Nothing in the tree implements it; today the equivalents are task state, review files, the workflow record, and the `herdctl observe` projection. See [Missions and Lifecycle](Missions-and-Lifecycle.md) and [Herdr](Herdr.md).
