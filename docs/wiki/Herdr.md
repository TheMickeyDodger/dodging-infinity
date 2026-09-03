# Herdr

Back to [Home](Home.md). Labels are defined on the Home page.

## What Herdr is and is not

**[IMPLEMENTED / PROVEN]** Herdr is the engineering organization inside an engineering mission. When a mission requires software engineering, the handoff creates an isolated Herdr with four roles: Supervisor, Lead, Executor, and Reviewer. Herdr starts from a bounded handoff, which the Operator has already translated from human intent, and turns it into independently reviewed engineering work with observable verification evidence. The Supervisor decomposes further inside that boundary; Herdr never receives an unbounded objective.

Herdr is not the mission control plane. It does not own mission identity, authority, lifecycle, evidence policy, scheduling, or delivery receipts. Dodging Infinity governs the mission; Herdr engineers inside it. Herdr is also not the product: a Herdr UI would be a UI for the engineering organization, not for the fabric. Each Herdr owns a finite scope: one repository, one top-level task, one effective rule set, one runtime state, explicit acceptance criteria, and observable verification evidence.

The core loop is understand, define, decompose, solve, challenge, validate, deliver. The operating surface (setup, presets, rules, the mission contract, the task lifecycle, the command list) is on the [Herdr operations](../reference/herdr-operations.md) page.

## Supervisor, Lead, Executor, Reviewer

**[IMPLEMENTED / PROVEN]**

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

- The Supervisor is the first strategy-bearing component. It owns decomposition, role assignment, execution planning, sequencing, engineering strategy, and the validation workflow. The mission handoff binds destination and boundaries; the Supervisor determines the engineering route.
- The Lead owns acceptance and completion. It briefs the Executor, validates the Reviewer's decision with `herdctl review-decision`, and persists it.
- The Executor implements the smallest coherent change that satisfies the brief, runs the verification command, checks its own diff, and reports evidence rather than confidence. It stays in the same session across rejection rounds.
- The Reviewer challenges the result independently. It is read-only, it does not need write access to `.herd/state/`, and it returns exactly one canonical token.

Bootstrap boundaries are explicit: a role is prevented from inferring engineering work from repository state, verification commands, or shared memory before it receives an explicit task or delegation. Long-running rejection and correction loops are intentional when they keep producing useful engineering evidence.

## The engineering handoff

**[IMPLEMENTED / PROVEN]** The Operator, not the human, normally constructs the handoff. A valid handoff carries the objective, repository context, known constraints, applicable rules, desired outcome, and unresolved questions. It does not carry the engineering execution plan; that belongs to the Supervisor. Prefer "investigate and resolve the authentication timeout; backward compatibility required; no schema change; add regression coverage; verify the existing suite" over "modify file X, add function Y." The Operator defines the destination and Herdr determines the route.

In the DI-REMOTE-2 path, the Broker's first dispatch is the byte-exact stored handoff, and a corrective follow-up is a bounded corrective brief, never a technical solution. The target Herdr bootstraps unattended inside the isolated managed workspace, with all four roles registered and interactive-ready before engineering proceeds; the historical external-target mountain recorded that receipt in production.

A mission can also be created directly through the deterministic primitives (`herdctl mission create`, `herdctl mission show`, `herdctl task --mission`), documented on the [Herdr operations](../reference/herdr-operations.md) page.

## Reviewer independence

**[IMPLEMENTED / PROVEN]** The Reviewer is a separate role in a separate session, ideally on a different model. The `max-quality` preset pairs a Claude Executor with a GPT Reviewer for that reason, and the v0.7.0 certification herd ran its independent Reviewer on `gpt-5.6-sol` while Supervisor and Executor ran on `claude-fable-5-1`. The Reviewer contract requires exactly one canonical terminal decision, `HERD_DECISION: APPROVE` or `HERD_DECISION: REJECT`; synonyms are not accepted. The Lead validates the decision with `herdctl review-decision`, which reads the Reviewer's transcript, records a new round, and persists the review file; malformed output returns `"valid": false` and the Lead re-prompts the same Reviewer session rather than interpreting it. Rejection can cause additional engineering iterations until the work genuinely satisfies the mission.

## What a Reviewer APPROVE does and does not mean

**[IMPLEMENTED / PROVEN]** An APPROVE means the target's own review process ran and concluded. It is target-produced evidence, and in v0.7.0 verification it is one of eight conjuncts, never independent verification. Herdr lifecycle COMPLETE alone can never verify a workflow, and the fresh verification turn's `verified_result` is necessary but never sufficient. The historical external-target mountain is the concrete case: target Herdr task `20260830-094026-9fef2d` reached COMPLETE and a canonical target Reviewer APPROVE was recorded, and the workflow still correctly terminated BLOCKED at `broker_verification_policy_drift` because the Broker's verification gate found genuine post-dispatch policy drift. The full gate is on [Evidence and Verification](Evidence-and-Verification.md).

**[PLANNED / TARGET]** The target keeps that rule and generalizes it: Reviewer approval is one proof requirement among those declared at authorization, the Evidence Graph records it, and mission verification is a separate Reconciler-confirmed state. Reviewer APPROVE matters. It is never, by itself, authoritative mission verification.

## Operating Herdr today

**[IMPLEMENTED / PROVEN]** In brief; the full surface is on the [Herdr operations](../reference/herdr-operations.md) page.

- Initialize a repository with `herdctl init --alias ALIAS --preset max-quality --test-command 'CMD'`, or set the verification command later with `herdctl set-test`.
- Dispatch with `herdctl task 'OBJECTIVE' --repo ALIAS` or `herdctl task --mission --repo ALIAS`. Task-local rules are `--rule`, repeatable.
- Inspect with `herdctl status`, `herdctl task-status`, `herdctl health`, and `herdctl observe`; see the [observability](../reference/observability.md) page.
- Complete with `herdctl task-complete --repo ALIAS --checkpoint-file FILE`; completed context is checkpointed to `.herd/memory/task-history.md`.
- Every repository receives isolated workspace, task state, mission state, context, memory, runtime configuration, and Git approval tokens.
- Delivery stays behind the human gates on the [human Git gates](../reference/human-git-gates.md) page. Herdr roles never commit or push without them and never use `--no-verify`.
