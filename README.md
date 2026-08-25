<p align="center">
  <img src="assets/brand/banner.svg" alt="Dodging Infinity — Bounding the infinite to the finite." width="100%">
</p>

# Dodging Infinity v0.6.1

[![CI](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml/badge.svg)](https://github.com/TheMickeyDodger/dodging-infinity/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/TheMickeyDodger/dodging-infinity)](https://github.com/TheMickeyDodger/dodging-infinity/releases/latest)

> **Bounding the infinite to the finite.**

**Any problem. Any repo. Any issue.**

Dodging Infinity is an engineering orchestration system built around Herdr for turning human intent into bounded, isolated, and verifiable engineering work.

[v0.6.1](https://github.com/TheMickeyDodger/dodging-infinity/releases/tag/v0.6.1) builds on the durable mission boundary established in v0.6.0 with a repository-level Codex Operator contract, an explicit operating protocol, and strictly read-only health and observability surfaces. Structured missions carry an objective, constraints, rules, acceptance criteria, and verification requirements into Herdr's Supervisor, Lead, Executor, and Reviewer workflow.

The operating contract makes the natural Codex CLI loop explicit: a human speaks to Codex normally, Codex investigates and formulates a bounded mission, Herdr performs the engineering work, and Codex inspects the result before approaching the existing human-gated commit, push, and release boundaries.

The goal is not merely multi-agent coding. Dodging Infinity creates a reliable boundary between human intent, model reasoning, autonomous execution, adversarial review, and verified outcomes.

## Why Dodging Infinity?

Most agent systems ask a model to do more.

Dodging Infinity does something different: it makes the problem smaller.

Instead of giving one model an expanding context and hoping it can reason across everything, Dodging Infinity recursively turns a large objective into bounded units with explicit scope, rules, ownership, dependencies, and validation.

That means the system can scale outward without silently expanding the authority of any individual agent.

The intelligence is replaceable. The orchestration contract is not.

## Target architecture

```mermaid
flowchart TD
    A[Human intent] --> B[Codex CLI Operator]
    B --> C[Herdr Mission]
    C --> D[Supervisor]
    D --> E[Lead]
    E --> F[Executor]
    E --> G[Reviewer]
    F --> H[Implementation]
    G --> I[Validation]
    H --> J[Verified outcome]
    I --> J
    J --> K[Codex CLI Operator]
    K --> L{Result complete?}
    L -- No --> C
    L -- Yes --> M[Human commit approval]
    M --> N[Codex executes commit]
    N --> O[Human push approval]
    O --> P[Codex executes push]
```

The architecture deliberately separates responsibilities:

- **Human** defines intent and retains delivery authority.
- **Codex** is the persistent outer operator.
- **Herdr** owns bounded engineering execution.
- **Supervisor** coordinates.
- **Lead** owns acceptance and completion.
- **Executor** implements.
- **Reviewer** challenges the result independently.
- **Git gates** enforce human authorization at delivery boundaries.

## What v0.6.1 provides

- **Operational readiness** — `herdctl health [--repo NAME]` checks configuration, server reachability, runtime state, expected live agents, and task-state readability without repairing or mutating the repository.
- **Read-only observability** — `herdctl observe [--repo NAME] [--json]` projects repository identity, workforce configuration, mission and task state, runtime topology, child dependencies, reviews, artifacts, and recent task summaries through a schema-versioned snapshot.
- **Bounded diagnostics** — Health and observation probes cap reads, scans, and live-agent queries; missing or malformed inputs become actionable diagnostics instead of unbounded work or tracebacks.
- **Codex Operator contract** — Repository-level `AGENTS.md` defines Codex as the persistent human-facing operator and Herdr as the engineering execution layer.
- **Operator protocol** — `OPERATOR_PROTOCOL.md` defines bounded handoffs, plan-scoped autonomy, completion review, recovery, and separate human delivery authorization.
- **Operator-to-Herdr mission boundary** — Added a durable mission contract that carries objective, constraints, rules, acceptance criteria, and verification requirements into Herdr.
- **Mission CLI** — Added `herdctl mission create`, `herdctl mission show`, and `herdctl task --mission`.
- **Herdr execution ownership** — Herdr remains the canonical engineering engine through Supervisor, Lead, Executor, and Reviewer roles.
- **Simplified orchestration model** — Removed the experimental Mission Control layer instead of building a second orchestration system beside Herdr.
- **Full mission envelope delivery** — The complete structured mission is delivered to the Supervisor while mission rules remain enforceable through task policy.
- **Stronger bootstrap boundaries** — Agents are explicitly prevented from inferring engineering work from repository state, verification commands, or shared memory before receiving an explicit task or delegation.
- **Deterministic review protocol** — Reviewer decisions remain canonical, persisted, and validated through Herdr's existing review gates.
- **Human-controlled delivery** — Commit, branch push, and release-tag push operations remain protected behind deterministic one-shot human authorization gates.
- **Improved push approval lifecycle** — `git push --dry-run` no longer consumes a one-shot approval.
- **Release-tag authorization** — Annotated tag pushes can be bound to the exact tag ref and tag-object SHA through `herdctl approve-push --tag` and `herdctl push-tag`.
- **Repository isolation** — Agents remain bounded to their assigned repository and task scope.
- **Model/runtime flexibility** — Operator and Herdr runtimes remain replaceable components behind stable orchestration contracts.

## What v0.6.1 does today

v0.6.1 provides the durable mission representation and dispatch path into Herdr, a documented Codex Operator workflow around that boundary, and read-only commands for checking readiness and observing current state.

A mission can be created explicitly:

```bash
herdctl mission create \
  "Fix the authentication bug" \
  --constraint "Do not change the database schema" \
  --rule "Preserve backward compatibility" \
  --acceptance "Authentication tests pass" \
  --verification "python3 -m unittest discover -s tests"
```

Inspect it:

```bash
herdctl mission show
```

Then dispatch the complete mission into Herdr:

```bash
herdctl task --mission
```

Herdr receives a deterministic envelope containing:

```text
OBJECTIVE
Fix the authentication bug

CONSTRAINTS
- Do not change the database schema

RULES
- Preserve backward compatibility

ACCEPTANCE CRITERIA
- Authentication tests pass

VERIFICATION
- python3 -m unittest discover -s tests
```

The Supervisor then owns execution through the normal Herdr hierarchy.

## Next milestone: the complete Codex operator loop

The intended user experience is deliberately simpler than the machinery underneath it.

```text
Human
  |
  v
Codex CLI
  |  understand
  |  investigate
  |  clarify when necessary
  |  formulate mission
  v
Herdr
  |  Supervisor
  |      |
  |     Lead
  |      |
  |  +----------+
  |  | Executor |
  |  | Reviewer |
  |  +----------+
  |
  |  execute
  |  challenge
  |  validate
  v
Codex CLI
  |  inspect result
  |  inspect diff
  |  inspect validation evidence
  |
  +-- incomplete --> formulate follow-up mission --> Herdr
  |
  +-- complete ----> prepare delivery
                      |
                      v
                Human commit approval
                      |
                      v
                Codex -> git commit
                      |
                      v
                 Human push approval
                      |
                      v
                 Codex -> git push
```

In normal use, the human should not need to manually construct `herdctl mission create` or `herdctl task --mission` commands.

Those commands are the stable machinery underneath the operator experience.

The remaining operator-loop work is:

### Repository-level Codex contract

Ship an `AGENTS.md` that establishes Codex as the persistent outer operator.

Codex should:

- receive human intent in natural language
- inspect and understand the repository
- gather relevant implementation context
- ask the human questions when requirements are genuinely ambiguous
- translate the request into a rigorous Herdr mission
- dispatch engineering work through Herdr
- avoid becoming a replacement Executor
- inspect Herdr's completed work
- formulate follow-up missions when necessary
- drive the terminal toward delivery only after the result is verified

### Operator guardrails

Use repository-level Codex hooks to reinforce the operator boundary.

The goal is to prevent Codex from casually bypassing Herdr and implementing source changes directly when the task belongs inside the Herdr engineering workflow.

The intended layers are:

```text
AGENTS.md
    |
    +--> tells Codex what it SHOULD do

Codex repository hooks
    |
    +--> block operator actions it SHOULD NOT do

Herdr Git guards
    |
    +--> enforce the final commit/push safety boundary
```

The Codex hooks are an operator guardrail, not a replacement for Herdr's deterministic Git protections.

### Deterministic handback

Add a blocking primitive such as:

```bash
herdctl wait
```

The intended behavior is:

```text
Codex dispatches mission
        |
        v
herdctl task --mission
        |
        v
herdctl wait
        |
        | Herdr works
        |
        +--> COMPLETE
        +--> ABORTED
        +--> ERROR
        |
        v
command returns
        |
        v
same Codex session continues reasoning
```

No second orchestration service, callback server, Ghostty automation layer, or replacement Herdr runtime is required.

### Completion loop

When control returns from Herdr, Codex should inspect:

- task state
- task checkpoint
- repository diff
- changed files
- validation output
- acceptance criteria
- repository rules
- Reviewer outcome

If the mission is not satisfied, Codex should **not fix the code directly**.

It should create another bounded Herdr mission.

If the mission is satisfied, Codex prepares the result for human-controlled delivery.

### Human-gated delivery

The final interaction should look like this:

```text
Codex:
Herdr completed the mission.

Validation:
- 64 tests pass
- acceptance criteria satisfied
- Reviewer approved

Changed files:
- example.py
- tests/test_example.py

Proposed commit:
Fix authentication state validation

Authorize this commit?

Human:
yes
```

After explicit human confirmation, Codex can invoke the existing non-interactive authorization path:

```bash
herdctl approve-commit --yes
```

and then:

```bash
git commit -m "Fix authentication state validation"
```

Push remains a separate human decision.

The same pattern applies:

```text
Codex:
Authorize push to origin/main?

Human:
yes
```

Then:

```bash
herdctl approve-push --yes
git push
```

Human authority remains outside the autonomous engineering loop.

The required technical primitives for this architecture have been validated. The remaining work is integration, policy, and end-to-end testing rather than another orchestration architecture.

## How Dodging Infinity works

Dodging Infinity starts with an unbounded human objective and turns it into a bounded engineering mission.

Today, v0.6.1 provides the durable mission representation, Herdr dispatch boundary, Codex Operator contract, and read-only operational visibility.

The repository-level operator contract places that translation behind Codex CLI.

Codex is responsible for:

1. understanding the objective
2. investigating the repository
3. gathering context
4. resolving ambiguity with the human
5. constructing a bounded mission
6. dispatching that mission to Herdr
7. waiting for Herdr to finish
8. inspecting the result
9. deciding whether another mission is needed
10. preparing verified work for human-gated delivery

Herdr remains responsible for engineering execution.

The Supervisor coordinates, the Lead owns acceptance, Executors implement changes, and Reviewers challenge the result before completion.

Each Herdr owns a finite scope:

- one repository
- one top-level task
- one effective rule set
- one runtime state
- explicit acceptance criteria
- observable verification evidence

The core loop is:

**understand -> define -> decompose -> solve -> challenge -> validate -> deliver**

The same bounded-work model can apply beyond conventional software engineering wherever complex objectives can be decomposed into isolated units with observable evidence and explicit validation.

## Model and runtime agnosticity

Dodging Infinity separates **responsibilities from models**.

The outer operator and Herdr roles are logical responsibilities rather than permanent model assignments.

The target architecture uses Codex CLI as the outer operator, while Herdr roles such as Supervisor, Lead, Executor, and Reviewer remain independently configurable.

Any model/runtime supported by Herdr can be assigned to an execution layer:

```text
Supervisor  -> Model A
Lead        -> Model B
Executor    -> Model C
Reviewer    -> Model D
```

or:

```text
Supervisor  -> Model X
Lead        -> Model X
Executor    -> Model X
Reviewer    -> Model X
```

Different providers, reasoning profiles, permission modes, context sizes, and tool capabilities can therefore be composed within the same Dodging Infinity system.

The orchestration contract belongs to the **role**.

The model is a replaceable execution engine for that role.

Presets such as `max-quality`, `all-claude`, and `conservative` are tested convenience configurations, not architectural requirements.

A new model or runtime can participate at any Herdr layer once it can be launched and controlled through Herdr.

This allows Dodging Infinity to select different forms of intelligence for different bounded problems while preserving the same decomposition, isolation, review, dependency, and validation machinery.

## Target max-quality topology

```text
You
 |
Codex CLI Operator
 |
Herdr Mission
 |
Claude Fable 5 — Supervisor
 |
Claude Opus 5 — Lead
 |
 +---------------------------------+
 | Adversarial Executor Pod        |
 |                                 |
 | Claude Fable 5 — Executor       |
 |              ↕                  |
 | GPT-5.6 Sol High — Reviewer     |
 | Read-only validation role       |
 +---------------------------------+
 |
Lead verification
 |
Codex result inspection
 |
Human commit gate
 |
Codex commit
 |
Human push gate
 |
Codex push
 |
Remote
```

The Reviewer remains read-only.

The deterministic harness/Lead persists its review transcript; the Reviewer does not need write access to `.herd/state/`.

## Upgrade an existing Herdr repo to v0.6.1

After updating Dodging Infinity and reinstalling the `herdctl` wrapper:

```bash
cd ~/code/internal

herdctl upgrade --repo example-repo
herdctl doctor --repo example-repo
```

v0.6.1 preserves the mission boundary and adds operator-facing visibility:

1. Define a bounded mission containing the objective, constraints, repository rules, acceptance criteria, and verification expectations.
2. Persist it through `herdctl mission create`.
3. Dispatch it through `herdctl task --mission`.
4. Herdr executes the mission through its Supervisor, Lead, Executor, and Reviewer workflow.
5. Codex inspects task, checkpoint, diff, verification, and Reviewer evidence before delivery.
6. `herdctl health` reports operational readiness and `herdctl observe` reports a bounded point-in-time snapshot without changing workflow state.
7. Human commit, push, and release gates remain required for delivery.

The Codex Operator translates normal human conversation into the bounded handoff described by steps 1 through 3; the mission CLI remains available as the deterministic persistence and dispatch boundary.

Existing Herdr safety boundaries, repository isolation, review validation, and Git authorization controls remain intact.

## Target operator experience

For a new or existing repository, the target workflow is:

1. Open the repository in Codex CLI.
2. Describe the objective in natural language.
3. Codex investigates the codebase.
4. Codex asks questions if necessary.
5. Codex creates and dispatches the Herdr mission automatically.
6. Herdr executes the bounded engineering workflow.
7. Control returns to the same Codex session.
8. Codex inspects the result.
9. Codex either dispatches follow-up work or prepares the verified change for delivery.
10. The human explicitly authorizes commit.
11. Codex executes the commit.
12. The human separately authorizes push.
13. Codex executes the push.

Example human objective:

```text
I want to fix the authentication bug in this repository.

Investigate the cause, implement the correct fix, add verification,
and prepare the change for review.

Do not change the database schema.
```

The target Codex operator converts that intent into a structured Herdr mission containing:

- objective
- constraints
- repository rules
- acceptance criteria
- verification requirements

Herdr then manages execution through:

```text
Supervisor
    |
Lead
    |
+-----------+
| Executor  |
| Reviewer  |
+-----------+
```

## Repository setup

The underlying repository setup remains:

```bash
herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'npm test && npm run build'
```

That creates the isolated Herdr workspace, repository state, runtime configuration, and Git authorization boundaries required for execution.

If you do not yet know the verification command:

```bash
herdctl init --alias my-repo --preset max-quality
herdctl set-test 'npm test && npm run build' --repo my-repo
```

## Presets

Presets configure the runtime assignments, models, and permissions used by Herdr roles.

They do not change the orchestration model.

List available presets:

```bash
herdctl presets
```

Current built-ins:

```text
max-quality   Multi-model configuration optimized for adversarial execution and review
all-claude    Claude-based runtime configuration
conservative  Explicit approval-focused configuration
```

Apply a preset to an existing repository:

```bash
herdctl preset max-quality --repo example-repo
```

The orchestration hierarchy remains independent from the preset.

Presets only determine which runtimes, models, and permission profiles are assigned to each role.

## Strict Reviewer protocol

The Reviewer contract requires exactly one terminal line:

```text
HERD_DECISION: APPROVE
```

or:

```text
HERD_DECISION: REJECT
```

No synonym is accepted.

`ACCEPT`, `LGTM`, `PASS`, and similar alternatives are malformed.

After every Reviewer turn, the Lead is instructed to run:

```bash
herdctl review-decision --repo example-repo --reviewer reviewer1
```

Example deterministic output:

```json
{
  "valid": true,
  "decision": "APPROVE",
  "raw_token": "APPROVE",
  "round": 2,
  "review_file": "/.../.herd/state/reviews/<task>-round-02.md"
}
```

A malformed result produces:

```json
{
  "valid": false
}
```

The Lead must re-prompt the **same Reviewer session** for a canonical token rather than interpreting the synonym itself.

This also solves read-only Reviewer persistence: Herdr writes the captured transcript under `.herd/state/reviews/` while the Reviewer remains sandboxed read-only.

## Mission contract

The durable mission representation is stored under:

```text
.herd/state/mission.json
```

The current mission schema is conceptually:

```json
{
  "version": 1,
  "objective": "Fix authentication bug",
  "constraints": [
    "Do not change database schema"
  ],
  "rules": [
    "Preserve backward compatibility"
  ],
  "acceptance_criteria": [
    "Authentication tests pass"
  ],
  "verification": [
    "python3 -m unittest discover -s tests"
  ]
}
```

Create a mission:

```bash
herdctl mission create \
  "Fix authentication bug" \
  --constraint "Do not change database schema" \
  --rule "Preserve backward compatibility" \
  --acceptance "Authentication tests pass" \
  --verification "python3 -m unittest discover -s tests"
```

Inspect it:

```bash
herdctl mission show
```

Dispatch it:

```bash
herdctl task --mission
```

Mission rules are also passed into the task policy so they remain part of Herdr's effective enforcement context.

## Human commit gate

Stage exactly what you want to commit, inspect it, then:

```bash
herdctl approve-commit --repo example-repo
```

The one-shot approval is bound to:

- repository/worktree
- branch
- HEAD
- exact staged diff hash
- short TTL

A changed staged diff, branch, or HEAD invalidates the token.

The deeper Git reference-transaction guard also blocks ordinary attempts to bypass pre-commit with:

```bash
git commit --no-verify
```

For future Codex operator mode, explicit human approval in the Codex conversation can be followed by:

```bash
herdctl approve-commit --yes
```

The `--yes` path is intended only when human confirmation has already occurred outside the command itself.

Codex may then execute the corresponding commit.

## Human branch push gate

After a local commit, inspect:

```bash
git status -sb
git log --oneline origin/main..HEAD
```

Then authorize exactly one push:

```bash
herdctl approve-push --repo example-repo
```

The approval screen shows:

- exact repo/path
- current branch and HEAD
- remote name and URL
- target ref
- upstream
- best-effort list of commits ahead

You type the repo alias to authorize exactly one push for a short TTL.

Then:

```bash
git push
```

The Git pre-push guard validates the exact branch, HEAD, remote, and target.

A dry-run:

```bash
git push --dry-run
```

does **not** consume the approval.

For branch pushes, authorization is consumed once the approved commit is observed on the approved remote-tracking ref.

## Human release-tag push gate

Release tags use their own exact-ref authorization path.

Create an annotated tag:

```bash
git tag -a vX.Y.Z -m "Dodging Infinity vX.Y.Z"
```

Authorize exactly that tag:

```bash
herdctl approve-push --tag vX.Y.Z
```

The approval binds to:

- repository
- current branch
- HEAD
- remote
- `refs/tags/vX.Y.Z`
- exact annotated tag-object SHA
- short TTL

Push through the Herdr-owned tag path:

```bash
herdctl push-tag vX.Y.Z
```

The one-shot tag approval is consumed only after the real tag transfer succeeds.

A dry-run does not consume it.

## Push guard note

Git itself allows a human or non-hook-aware runtime to bypass `pre-push` with:

```bash
git push --no-verify
```

For the currently proven workforce, Claude's global PreToolUse guard independently blocks `--no-verify` and force-push forms before Bash executes, while the Codex Reviewer remains read-only.

The role contracts also forbid bypassing the gate.

The planned Codex outer-operator integration will add equivalent repository-level guardrails around the operator role.

If a different write-capable runtime is later placed in an Executor seat, give it an equivalent runtime-level command guard if the same hard guarantee is required.

## Claude Auto Mode setup

Run once per Mac after installing or upgrading Herdr:

```bash
herdctl safety-install
```

It installs or maintains the Claude PreToolUse commit/push guard and ensures the user-level `autoMode.environment` contains:

```json
["$defaults"]
```

This preserves Claude's built-in conservative trust boundary around the working repository and configured remotes instead of broadening trust to unrelated repositories.

Existing user Auto Mode entries are preserved.

## Rules and task-local constraints

Dodging Infinity supports constraints at two scopes.

Repository rules are durable and apply to future tasks in that repository:

```bash
herdctl rules --repo example-repo
herdctl rules add "Never modify migrations" --repo example-repo
herdctl rules remove "Never modify migrations" --repo example-repo
```

Task-local rules apply only to one directly dispatched task:

```bash
herdctl task "Implement X" \
  --repo example-repo \
  --rule "Only modify README.md" \
  --rule "Do not commit"
```

Task-local rules are layered into that task's effective policy and do not persist into the repository's durable rule set.

Mission rules behave similarly: they are included in the full mission envelope and passed into the task policy for that dispatched mission.

## Task lifecycle

```text
IDLE -> ACTIVE -> COMPLETE
               -> ABORTED
               -> ERROR
```

Start a direct task:

```bash
herdctl task 'Implement X. Do not commit.' --repo example-repo
```

Or dispatch the current mission:

```bash
herdctl task --mission --repo example-repo
```

Inspect:

```bash
herdctl task-status --repo example-repo
herdctl status --repo example-repo
```

The Supervisor finishes with:

```bash
herdctl task-complete \
  --repo example-repo \
  --checkpoint-file .herd/state/task-checkpoint.md
```

Completed-task context is checkpointed to bounded:

```text
.herd/memory/task-history.md
```

The next top-level task can start with fresh runtime context while rejection rounds inside the same task remain long-running.

A blocking operator-friendly task wait primitive is planned for the next milestone.

## Idle-aware heartbeat

```text
15-minute tick
 |
 +-- no ACTIVE task ------------------> skip
 |
 +-- Supervisor working/blocked ------> skip
 |
 +-- ACTIVE + Supervisor idle/done ---> health-check
```

The heartbeat is aware of top-level task state and does not wake an idle Herdr when no engineering task is active.

## Multi-repo usage

```bash
herdctl task 'Fix auth' --repo example-repo
herdctl task 'Improve onboarding' --repo zenmo
herdctl status --repo another-repo
```

Every repository has its own:

- Herdr workspace
- task state
- mission state
- context
- memory
- runtime configuration
- Git approval tokens

Cross-repository engineering remains explicitly isolated.

## Main commands

```text
herdctl init [--alias NAME] [--preset PRESET] [--test-command COMMAND]

herdctl presets
herdctl preset PRESET --repo NAME
herdctl set-test COMMAND --repo NAME

herdctl upgrade --repo NAME [--preset PRESET]
herdctl repos [--prune]

herdctl safety-install
herdctl doctor --repo NAME
herdctl health --repo NAME
herdctl integrations --repo NAME

herdctl bootstrap --repo NAME [--force]

herdctl mission create "OBJECTIVE" \
  [--repo NAME] \
  [--constraint CONSTRAINT ...] \
  [--rule RULE ...] \
  [--acceptance CRITERION ...] \
  [--verification COMMAND ...]

herdctl mission show [--repo NAME]

herdctl task "..." \
  --repo NAME \
  [--rule RULE ...] \
  [--rejection-drill]

herdctl task --mission \
  --repo NAME \
  [--rule RULE ...] \
  [--rejection-drill]

herdctl task-status --repo NAME
herdctl task-complete --repo NAME --checkpoint-file FILE
herdctl task-abort --repo NAME [--reason REASON]

herdctl rules --repo NAME
herdctl rules add RULE --repo NAME
herdctl rules remove RULE --repo NAME

herdctl review-decision --repo NAME --reviewer reviewer1

herdctl clear-contexts --repo NAME

herdctl approve-commit --repo NAME
herdctl approve-commit --repo NAME --yes

herdctl approve-push \
  --repo NAME \
  [--remote origin] \
  [--target-branch BRANCH]

herdctl approve-push \
  --repo NAME \
  [--remote origin] \
  --tag TAG

herdctl push-tag TAG --repo NAME

herdctl status --repo NAME
herdctl observe --repo NAME [--json]
herdctl read ROLE --repo NAME
herdctl prompt ROLE "..." --repo NAME

herdctl heartbeat --once --repo NAME
herdctl restart-heartbeat --repo NAME
```

## Inspecting a herd: doctor vs status vs health vs observe

- `doctor` — environment/tooling probe: are the binaries, runtime kinds, and git guard hooks installed here?
- `status` — task + agent state display: what is this herd currently doing?
- `health` — operational readiness probe: is THIS repository's herd configured, reachable, and usable right now?
- `observe` — full read-only projection: one bounded, schema-versioned snapshot of everything the herd's persisted and live-queryable state says right now, as concise human text or canonical JSON.

`herdctl health [--repo NAME]` is strictly read-only. It checks the herd
configuration, Herdr server reachability, runtime state, the herd's agents,
and that task state is readable. The agent check derives the expected logical
roles from the config's `orchestration` counts, fails if any expected role is
absent from runtime state, and fails if any recorded agent cannot be resolved
to a live Herdr agent; extra agents beyond the config are reported as
information. Agent workflow states (idle, working, done, blocked) are also
information — a blocked agent alone does not fail health. Missing,
unreachable, malformed, or unknown infrastructure fails with an actionable
remedy and a nonzero exit; a healthy herd exits 0.

Live-agent probing is bounded: at most 512 recorded agents are probed per
run (a hard constant, far above any realistic topology), with expected
roles probed first. If a runtime map exceeds the bound, health fails with
a count of the unprobed entries rather than reporting READY on the
strength of agents it never verified.

## Observability layer: herdctl observe

```text
herdctl observe [--repo NAME] [--json]
```

`observe` builds one strictly read-only, point-in-time projection of a
repository's herd. Human mode prints a concise summary; `--json` prints the
canonical projection (`json.dumps(obs, indent=2)`) and nothing else on
stdout. It always exits 0 when an observation is produced — including a
fully PARTIAL one — because observe is a reporting command, not a gate. The
single exception is an unresolvable `--repo` reference, which is a usage
error (exit 2).

### JSON contract (schema_version 1)

The top-level key set is fixed and built structurally — a destroyed input
can never make a section vanish:

```text
schema_version, generated_at, completeness, repository, config, mission,
task, runtime, agents, children, reviews, artifacts, recent_tasks, legacy,
diagnostics
```

Every section carries a `state` field from a closed vocabulary:
`available | missing | malformed | unreadable | unavailable | empty`.
`diagnostics` is a list of `{source, state, detail}` — one entry per source
that is not cleanly available, plus one per applied listing truncation,
exhausted directory-scan budget, or capped dirty-path count (string
truncation is indicated in place by a visible ellipsis rather than by a
diagnostic).
`completeness` is `"PARTIAL"` when any diagnostic records a `malformed`,
`unreadable`, or `unavailable` source (truth exists that could not be
seen) and `"COMPLETE"` otherwise; cleanly observed absences (`missing`,
`empty`) do not demote it. A listing truncation whose total is exact
(listed agents, children, review files, recent tasks) is disclosed as an
`available` diagnostic and does not demote; an exhausted directory-scan
budget or a capped dirty-path count makes the derived numbers lower
bounds, so those are disclosed as `unavailable` diagnostics and do demote. Completeness is visibility
only — it never affects the exit code and never gates, repairs, or
controls anything.

Live-agent state is allowlisted: each listed agent is exactly
`{logical, agent, status, probe}` with `probe` in
`ok | missing | unknown | unprobed`; raw runtime payloads are never
emitted. Review files are referenced through bounded metadata only
(`file, round, decision, size, mtime`). `decision` comes from the
canonical `Protocol token:` header that `herdctl review-decision` writes
into each persisted round artifact: when the artifact's `## Transcript`
marker exists and a header line precedes it, that header is authoritative —
`APPROVE`/`REJECT` resolve to that value and any other recorded token
(`MISSING`, `ACCEPT`, ...) yields `null` without consulting the transcript,
so prose quoting the protocol can never override the canonical record. An
exact contiguous `HERD_DECISION: APPROVE` / `HERD_DECISION: REJECT` token
scan is the fallback only when no `Protocol token:` line of any shape
exists in the preamble — a malformed (e.g. indented) preamble header is
authoritative-but-invalid, yielding `null` and suppressing the fallback so
transcript text can never decide over an unparseable record. Header-shaped
lines are never honoured outside a canonical preamble, and a pane-wrapped
token with no header yields `null`. Transcripts are never parsed into
findings and never re-emitted.

### Hard bounds

All bounds are module constants in `herdr/observe.py`, never derived from
repository input: state files larger than 1 MiB are refused; at most 64
live `herdr agent get` probes per run (config-expected roles probed
first); at most 32 listed agents, 10 recent tasks, 40 review files, 32
listed children, and 16 artifacts; every projected string is truncated to
200 characters total including a visible ellipsis; directory scans spend
at most 2000 entries of budget before sorting, and an exhausted budget is
always disclosed as an `unavailable` diagnostic; `dirty_file_count` is
capped at 2000 porcelain status lines — when the cap applies the
`dirty_file_count_capped` flag is set, an `unavailable` diagnostic
discloses that the true count is higher, and the human render labels the
number as capped. `children.count` is the
exact matched-record count (the record list is memory-bounded by the
1 MiB file limit); only its listing is truncated. The `*brief-*.md`
artifact glob is newest-first over scanned entries — best-effort when the
scan budget is exhausted, which the diagnostic states. Artifact
`freshness` (`fresh`/`stale` at 24 h) is a label only.

### Point-in-time limitations and non-goals

An observation is a snapshot, not a stream: nothing is watched, tailed, or
subscribed to. Child status under `children` is the recorded child record
from this repository's `.herd/state/children.json`, not resolved child
liveness — observe never reads another repository's filesystem. The legacy
`events.jsonl` is reported only under `legacy` as a stale journal from the
removed Mission Control stack; it never feeds any activity or "current"
field.

Non-goals, explicitly: no mutation (observe leaves `.herd/`, the worktree,
and `.git` — including `.git/index`, via `--no-optional-locks` —
byte-for-byte unchanged), no repair, no agent prompting, no gating, no
control, no event stream, no TUI, and no server.

## Codex Gateway: codexgw

`codexgw` is a local, transport-neutral interface boundary: it takes human
intent typed (or piped) in a terminal and routes it ONLY into the existing
Codex Operator workflow — the locally installed `codex` CLI running this
repository's Operator contract (`AGENTS.md` + `OPERATOR_PROTOCOL.md`).

```text
Human terminal
      |
      v
   codexgw          validate target repo -> invoke codex CLI -> render result
      |
      v
  Codex CLI         runs the Operator contract (AGENTS.md)
      |
      v
    Herdr           engineering execution (unchanged; codexgw never touches it)
```

The gateway sits strictly ABOVE the operator: it does not understand,
construct, dispatch, monitor, or control Herdr work. The static suite
enforces this architecturally — the `codex_gateway/` package and `codexgw.py`
must never import `herdr` or `herdctl` (verified by AST walk, token scan,
and a behavioral import probe).

### Local terminal workflow

```bash
codexgw investigate the flaky login test and propose a fix
echo "summarize the current mission state" | codexgw
codexgw --repo ~/src/myproject "add regression coverage for the parser"
codexgw --json describe the last verification run
```

Intent may arrive as arguments or on piped stdin; after normalization the
two routes produce an identical request (argument words are joined with
single spaces and stripped of leading/trailing whitespace; stdin text is
stripped the same way but preserves interior newlines and spacing that
arguments cannot express). Intent must be valid UTF-8: piped stdin is
read as bytes and decoded strictly, and non-decodable bytes or
non-encodable text are refused as an invalid request before Codex is
ever invoked. `--repo` defaults to the current directory.
The target must be a directory inside a git worktree containing
`AGENTS.md` and `OPERATOR_PROTOCOL.md`; the two files are looked for at
the resolved `--repo` path itself, not at the git toplevel — so run
`codexgw` from the repository root, or pass `--repo <root>` explicitly
when working from a subdirectory. Validation happens before Codex is
invoked and failures name the exact check and path.

Exit codes are deterministic: `0` completed, `2` invalid request,
`3` codex unavailable, `4` codex failed, `5` malformed output. Stdout
carries the final Operator message (or, with `--json`, the full versioned
result contract); errors are a single actionable stderr line. These
guarantees cover the byte boundaries the gateway itself controls (intent
in, Codex streams out); as with any Python CLI, printing a valid
non-ASCII message can still fail if the interpreter's stdout encoding is
overridden to one that cannot represent it (e.g. `PYTHONIOENCODING=ascii`).

### Session continuation

The gateway is stateless. When Codex reports a session handle, `codexgw`
prints it on stderr and includes it in `--json` output; continuing the
conversation is the caller passing it back in:

```bash
codexgw --resume SESSION_ID "yes, proceed with option two"
```

If no session handle appears in Codex output, `session_id` is reported as
null — never invented or inferred.

### Trust limitations

- `codex exec --json` documents its output as JSONL events but not the
  concrete event schema. The gateway declares the exact event shapes it
  recognizes as named constants in `codex_gateway/codex_adapter.py` (a
  declared compatibility surface). A human must validate that surface
  against the installed Codex CLI version; if the event schema drifts,
  the gateway fails closed with `malformed_output` naming the surface
  rather than guessing. Every result additionally carries an
  always-present `unrecognized_event_lines` count (also in `--json`) of
  the non-blank output lines that matched no declared shape; when it is
  above zero in text mode, a one-line stderr diagnostic discloses it —
  a partially-unparsed stream is never presented as fully understood.
  Codex output is likewise read as bytes and decoded as strict UTF-8: an
  undecodable stream maps to `malformed_output` with its own error code
  (`codex_output_not_utf8`), and non-UTF-8 bytes quoted from stderr in
  error detail are shown escaped with explicit disclosure — never
  silently replaced.
- The gateway inherits the ambient environment, and with it the user's
  own Codex configuration and credentials; it implements no
  authentication and reads no credential or configuration file itself.
- Codex sandbox-weakening and check-bypassing flags (`--dangerously-*`,
  `--skip-git-repo-check`, `--sandbox`/`-s`, `--add-dir`, `--ephemeral`,
  `--ignore-rules`, `--ignore-user-config`) are banned by an in-code
  guard over every constructed argv.
- Error details are bounded by a fixed constant; a capped detail is
  always flagged as truncated, never presented as complete.

### Non-goals, explicitly

No Herdr control (the gateway never imports, invokes, or reads Herdr,
herdctl, or `.herd` state), no networking or HTTP/WebSocket/socket
transport, no daemon/listener/queue/server, no authentication layer, no
persisted gateway state, and no deadline or duration-based cancellation —
the gateway waits for Codex for as long as Codex runs, and failure is
propagated transparently (exit status plus bounded stderr) rather than
managed.

## Roadmap

v0.6.1 establishes the operator contract and makes Herdr readiness and state visible without changing execution semantics. The next milestone focuses on closing the remaining operator-loop gaps:

- blocking `herdctl wait`
- deterministic Herdr-to-Codex handback
- tighter result-inspection and follow-up mission routing
- full end-to-end real-world operator-loop validation

The design constraint remains the same:

> Codex operates. Herdr engineers. Humans authorize delivery.

## License

Dodging Infinity is licensed under the [Apache License 2.0](LICENSE).
