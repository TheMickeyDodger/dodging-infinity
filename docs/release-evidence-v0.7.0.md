# Release evidence v0.7.0

Back to [Architecture](architecture.md) · [Operations](operations.md) · [Roadmap](roadmap.md)

**[IMPLEMENTED / PROVEN]** The release lineage from v0.6.1 to v0.7.0 and
the evidence behind each step, preserved in full. The authoritative
record is [CHANGELOG.md](../CHANGELOG.md); the near-term plan that
carries the release gate is the
[roadmap](roadmap.md). How
verification is gated, and why a Reviewer APPROVE is necessary but not
sufficient, is on the wiki
[architecture.md](architecture.md#10-evidence-and-verification) page.

## Foundation by v0.6.1

[v0.6.1](https://github.com/TheMickeyDodger/dodging-infinity/releases/tag/v0.6.1)
established the repository-level Codex Operator contract, explicit
operating protocol, and strictly read-only health and observability
surfaces.

- **Operational readiness**: `herdctl health [--repo NAME]` checks configuration, server reachability, runtime state, expected live agents, and task-state readability without repairing or mutating the repository.
- **Read-only observability**: `herdctl observe [--repo NAME] [--json]` projects repository identity, workforce configuration, mission and task state, runtime topology, child dependencies, reviews, artifacts, and recent task summaries through a schema-versioned snapshot.
- **Bounded diagnostics**: Health and observation probes cap reads, scans, and live-agent queries; missing or malformed inputs become actionable diagnostics instead of unbounded work or tracebacks.
- **Codex Operator contract**: Repository-level `AGENTS.md` defines Codex as the persistent human-facing operator and Herdr as the engineering execution layer.
- **Operator protocol**: `OPERATOR_PROTOCOL.md` defines bounded handoffs, plan-scoped autonomy, completion review, recovery, and separate human delivery authorization.
- **Operator-to-Herdr mission boundary**: A durable mission contract carries objective, constraints, rules, acceptance criteria, and verification requirements into Herdr.
- **Mission CLI**: `herdctl mission create`, `herdctl mission show`, and `herdctl task --mission`.
- **Herdr execution ownership**: Herdr remains the canonical engineering engine through Supervisor, Lead, Executor, and Reviewer roles.
- **Simplified orchestration model**: The abandoned Mission Control layer was removed rather than creating a second engineering orchestration system beside Herdr.
- **Full mission envelope delivery**: The complete structured mission is delivered to the Supervisor while mission rules remain enforceable through task policy.
- **Stronger bootstrap boundaries**: Agents are explicitly prevented from inferring engineering work from repository state, verification commands, or shared memory before receiving an explicit task or delegation.
- **Deterministic review protocol**: Reviewer decisions remain canonical, persisted, and validated through Herdr's review gates.
- **Human-controlled delivery**: Commit, branch push, and release-tag push operations remain protected behind deterministic one-shot human authorization gates.
- **Improved push approval lifecycle**: `git push --dry-run` no longer consumes a one-shot approval.
- **Release-tag authorization**: Annotated tag pushes can be bound to the exact tag ref and tag-object SHA through `herdctl approve-push --tag` and `herdctl push-tag`.
- **Repository isolation**: Agents remain bounded to their assigned repository and task scope.
- **Model/runtime flexibility**: Operator and Herdr runtimes remain replaceable components behind stable orchestration contracts.

## v0.6.2: Codex Gateway

[v0.6.2](https://github.com/TheMickeyDodger/dodging-infinity/releases/tag/v0.6.2)
added Codex Gateway v0.1: a local, transport-neutral interface boundary in
front of the existing Codex Operator workflow, with versioned
request/response contracts, fail-closed structured output, hermetic
regression coverage, and static enforcement of the gateway/Herdr
architectural boundary. Detail is in [operations.md](operations.md#the-codex-gateway).

## v0.6.3: Telegram Remote Operator MVP

[v0.6.3](https://github.com/TheMickeyDodger/dodging-infinity/releases/tag/v0.6.3)
added the implemented Telegram Remote Operator MVP. A trusted, allowlisted
Telegram user can submit intent from a phone, receive and approve or reject
a Codex plan, resume the same Codex session, query status, and receive the
verified result. The adapter has no direct path to Herdr or `herdctl`.

Real Telegram setup and traffic validation shipped in v0.6.3. The
adapter was exercised from an allowlisted private Telegram user against
the trusted MacBook with:

- real outbound Bot API traffic
- new and resumed Codex sessions
- bounded Codex plan generation
- the live Approve / Reject callback path
- live `editMessageReplyMarkup` approval attachment after complete plan
  delivery and durable binding
- status and verified-result delivery
- the fail-closed Telegram → Codex Gateway → Codex Operator boundary,
  with no direct Telegram path to Herdr or `herdctl`

Remote commit, push, PR, tag, release, deployment, and merge authorization
are not implemented today. Exact Telegram-native delivery approvals are
planned Phase-I work in the Remote Mission Fabric roadmap.

## v0.7.0: DI-REMOTE-2 Remote Target Repository Routing

v0.7.0 adds DI-REMOTE-2 Remote Target Repository Routing: an allowlisted
Telegram user can authorize one exact bounded cross-repository mission
while Dodging Infinity remains the permanent control and policy
repository. The complete lifecycle is proven hermetically. The evidence
identifiers, from the CHANGELOG:

- DI-REMOTE-2 release certification: fresh continuation task
  `20260901-165812-045b0c` completed normally after adversarial Reviewer
  correction rounds. The accepting review was persisted `valid=true`,
  `APPROVE`; the authoritative unchanged-tree discovery ran 2,048 tests in
  403.984 seconds with `OK (skipped=1)` and exit 0.
- Runtime stabilization, integrated: commit
  `d8ec2af409e4086f985be03371a872a84a3767ec` on branch
  `fix/runtime-terminal-reconciliation` (Herdr task `20260830-185309-4c3db7`,
  COMPLETE, final canonical Reviewer round 6 APPROVE) was reviewed and
  pushed before being integrated into `main` for v0.7.0. Its historical
  validation: focused regression 159/159; `tests/test_target_runtime.py`
  250/250; static checks PASS; Python 3.9.6 compile PASS; `git diff --check`
  PASS. The historical repository-wide LIVE working-tree loop stood at 35/37
  solely because pre-existing live `.herd` specimen assertions in
  `tests/test_hermetic_git.py` and `tests/test_reconcile_audit.py` predate
  that task.
- Clean-clone CI evidence: commits
  `52a97b71a3b5c9f20ff33d4feb1332284cd825b7` and
  `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f` made the CI hermetic; the
  final GitHub CI run `33330263889` is green across all four macOS/Ubuntu x
  Python 3.9/3.13 jobs.
- Certification topology: the continuation herd ran Supervisor on
  `claude-fable-5-1`, Lead on `opus`, Executor on `claude-fable-5-1`, and the
  independent Reviewer on `gpt-5.6-sol`.
- Automated validation of the unattended remote runtime hardening
  increment: 1778 tests with 0 skips, all static checks, and
  `git diff --check`.

Completed foundations at v0.7.0, as the README recorded them:

- durable Herdr mission boundary
- Supervisor → Lead → Executor → Reviewer orchestration
- repository isolation
- deterministic review protocol
- human commit/push/tag gates
- Codex Operator contract
- plan-scoped operator protocol
- `herdctl health`
- `herdctl observe`
- schema-versioned observation model
- Codex Gateway v0.1 local intent boundary
- live Codex Gateway compatibility validation
- Telegram Remote Operator MVP
- trusted Telegram identity allowlist and private-chat enforcement
- one-shot, fully bound plan approval and rejection
- resumed Codex sessions, status, meaningful errors, and verified-result delivery
- optional per-user macOS LaunchAgent baseline
- DI-REMOTE-2 Remote Target Repository Routing (part of v0.7.0;
  hermetically verified and historically exercised through target Herdr
  COMPLETE before the live mountain exposed post-dispatch policy drift and
  terminated BLOCKED; the corrected final-result path is now certified
  hermetically and adversarially, while separate artifact delivery remains
  outside that certification)

## The historical external-target mountain

One historical external-target mountain exercised the production path once.
It is bounded live evidence and it is terminal. The identifiers: workflow
`wf-2c901885473fc4781bf82296`, target Herdr task `20260830-094026-9fef2d`,
target baseline `3e1833d930723ef4f7220698c98155a925591d4d`, from a
natural-language Telegram request targeting an external repository issue.

What it proved: the request triggered a separate fresh restrictive planning
process and a valid Mission Authorization that preserved the exact human
request, bound the control repository and the target baseline, retained
`delivery_authority = none`, and required a one-shot Telegram button
approval. The Runtime independently claimed the durable authorization
without a Gateway engineering turn, materialized and trusted the isolated
target, prepared and validated the handoff, durably bound the target
engine, and bootstrapped Supervisor, Lead, Executor, and Reviewer.
Supervisor owned strategy; Lead, Executor, and Reviewer ran normally. The
target Herdr task reached COMPLETE, a canonical target Reviewer APPROVE was
recorded, and target observation refreshed from a stale `ACTIVE` reading
to `COMPLETE`.

How it ended: the workflow then exposed genuine post-dispatch policy drift
and correctly terminated BLOCKED at `broker_verification_policy_drift`.
`verified_result` and `result_delivery` remained null. No target Git
delivery occurred; the target stayed at baseline carrying an implementation
diff only. Everything downstream of the drift stop did not run in that
historical execution.

What happened next: the defects it exposed were closed, the Runtime
stabilization lineage was integrated into `main`, and the corrected
independent verification, `VERIFIED`/`COMPLETED`, and exactly-once
final-result path was certified hermetically and adversarially for v0.7.0.
A fresh post-fix live mountain is not used as release evidence.

## What the release evidence covers

- The complete DI-REMOTE-2 lifecycle, certified hermetically and
  adversarially on the stable release tree.
- Verified final-result delivery to Telegram, exactly once: never twice and
  never silently dropped.
- Live, once, on the historical mountain: real Telegram v2 traffic, GitHub
  target materialization, unattended target Herdr bootstrap, fresh
  installed-Codex role turns, and Supervisor-led execution through target
  Herdr COMPLETE with a canonical target Reviewer APPROVE.
- Human Git gates preserved on the live target (`commit: require-human`,
  `push: require-human`), with no delivery possible from a Mission
  Authorization.

## What it does not cover

- A fresh post-fix live mountain. None was run as release evidence.
- Separate artifact delivery. It is not claimed by this certification.
- Telegram-native commit, push, PR, tag, release, deploy, or merge
  authorization. Those remain local, human-authorized actions.
- The model a RUNNING agent uses. It is not observable through the agent
  interface, and the observation surface says so.
- Any claim about a mission other than the one historical mountain and the
  hermetic suites. The recorded codex-cli 0.149.0 telemetry limitation (A0)
  is stated verbatim in [SECURITY.md](../SECURITY.md).
