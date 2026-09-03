# Herdr operations

Back to [Wiki home](../wiki/Home.md) · [Reference index](README.md)

**[IMPLEMENTED / PROVEN]** The complete Herdr operating surface as it
ships in v0.7.0, relocated from the README without change to any command,
flag, path, or contract. What Herdr is, and what a Reviewer APPROVE means,
is on the wiki [Herdr](../wiki/Herdr.md) page. The delivery gates are on
[human-git-gates.md](human-git-gates.md); inspection commands are on
[observability.md](observability.md).

Every `herdctl` invocation below was checked against `herdctl.py --help`
and the subcommand help in this checkout.

## Repository setup

Initialize a repository:

```bash
herdctl init \
  --alias my-repo \
  --preset max-quality \
  --test-command 'npm test && npm run build'
```

If the verification command is not known yet:

```bash
herdctl init --alias my-repo --preset max-quality
herdctl set-test 'npm test && npm run build' --repo my-repo
```

The repository receives isolated Herdr runtime configuration and Git authorization boundaries.


## Upgrade an existing repository

```bash
cd ~/code/internal

herdctl upgrade --repo example-repo
herdctl doctor --repo example-repo
```

Existing Herdr safety boundaries, repository isolation, review validation, and Git authorization controls remain intact.

## Presets

List presets:

```bash
herdctl presets
```

Current built-ins:

```text
max-quality   Multi-model configuration optimized for adversarial execution and review
all-claude    Claude-based runtime configuration
conservative  Explicit approval-focused configuration
```

Apply one:

```bash
herdctl preset max-quality --repo example-repo
```

Presets assign runtimes/models/permissions.

They do not alter the orchestration hierarchy.

## Rules and constraints

Repository rules persist:

```bash
herdctl rules --repo example-repo

herdctl rules add \
  "Never modify migrations" \
  --repo example-repo

herdctl rules remove \
  "Never modify migrations" \
  --repo example-repo
```

Task-local rules:

```bash
herdctl task "Implement X" \
  --repo example-repo \
  --rule "Only modify README.md" \
  --rule "Do not commit"
```

Mission rules remain scoped to the dispatched mission.

## Mission contract

Mission state is persisted under:

```text
.herd/state/mission.json
```

Conceptually:

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

Create:

```bash
herdctl mission create \
  "Fix authentication bug" \
  --constraint "Do not change database schema" \
  --rule "Preserve backward compatibility" \
  --acceptance "Authentication tests pass" \
  --verification "python3 -m unittest discover -s tests"
```

Inspect:

```bash
herdctl mission show
```

Dispatch:

```bash
herdctl task --mission
```

## Task lifecycle

```text
IDLE -> ACTIVE -> COMPLETE
               -> ABORTED
               -> ERROR
```

Start:

```bash
herdctl task \
  'Implement X. Do not commit.' \
  --repo example-repo
```

Or:

```bash
herdctl task --mission --repo example-repo
```

Inspect:

```bash
herdctl task-status --repo example-repo
herdctl status --repo example-repo
```

Complete:

```bash
herdctl task-complete \
  --repo example-repo \
  --checkpoint-file .herd/state/task-checkpoint.md
```

Completed context is checkpointed to:

```text
.herd/memory/task-history.md
```

Long-running rejection/correction loops are intentional when they continue producing useful engineering evidence.

## Strict Reviewer protocol

The Reviewer contract requires exactly one canonical terminal decision:

```text
HERD_DECISION: APPROVE
```

or:

```text
HERD_DECISION: REJECT
```

Synonyms are not accepted.

After a Reviewer turn:

```bash
herdctl review-decision \
  --repo example-repo \
  --reviewer reviewer1
```

Example:

```json
{
  "valid": true,
  "decision": "APPROVE",
  "raw_token": "APPROVE",
  "round": 2,
  "review_file": "/.../.herd/state/reviews/<task>-round-02.md"
}
```

Malformed output produces:

```json
{
  "valid": false
}
```

The Lead re-prompts the same Reviewer session rather than interpreting malformed output itself.

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

The heartbeat observes active work without inventing work when no task exists.

## Multi-repo usage

```bash
herdctl task 'Fix auth' --repo example-repo
herdctl task 'Improve onboarding' --repo another-repo
herdctl status --repo third-repo
```

Every repository receives isolated:

- Herdr workspace
- task state
- mission state
- context
- memory
- runtime configuration
- Git approval tokens

The Telegram Remote Operator preserves this same isolation: one configured repository per adapter instance, with session and approval state bound to that repository's resolved path.

## Main Herdr commands

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

## Target max-quality topology

```text
Human
 |
Phone / Terminal
 |
Codex Gateway or direct Codex CLI
 |
Codex Operator
 |
Herdr Handoff
 |
Claude Fable 5: Supervisor
 |
Claude Opus 5: Lead
 |
 +---------------------------------+
 | Adversarial Executor Pod        |
 |                                 |
 | Claude Fable 5: Executor        |
 |              ↕                  |
 | GPT-5.6 Sol High: Reviewer      |
 | Read-only validation role       |
 +---------------------------------+
 |
Lead verification
 |
Codex independent inspection
 |
Human commit gate
 |
Codex commit
 |
Human push / PR gate
 |
Codex delivery
 |
GitHub
```

The Reviewer remains read-only.

The deterministic harness/Lead persists review evidence; the Reviewer does not need write access to `.herd/state/`.

## Runtime command safety

Git itself permits bypass forms such as:

```bash
git push --no-verify
```

Runtime-level command protections and role contracts complement the deterministic Git guards.

The Codex Gateway preserves the operator boundary.

It does not become a replacement delivery authority.

## Claude Auto Mode setup

Run once per Mac after installing/upgrading Herdr:

```bash
herdctl safety-install
```

This maintains the Claude runtime command guard while preserving existing user Auto Mode configuration.

## Direct Herdr mission workflow

A mission can still be explicitly created through the deterministic Herdr primitives:

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

Dispatch it:

```bash
herdctl task --mission
```

Herdr receives:

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

The Supervisor then owns engineering execution.

In normal operator use, however, humans should not need to manually construct this envelope.

Codex handles the translation from human intent into the Herdr handoff.
