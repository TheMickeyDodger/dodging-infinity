# Security Policy

## Supported versions

Security fixes are currently provided for the latest Dodging Infinity release line.

| Version | Supported |
| --- | --- |
| 0.6.x | Yes |
| < 0.6 | No |

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Use GitHub private vulnerability reporting for this repository when available. Include enough information to reproduce and understand the impact of the issue, including affected components, relevant configuration, and a minimal proof of concept when appropriate.

Please avoid including secrets, credentials, private repository contents, or unrelated user data in a report.

## Security-sensitive areas

Changes involving the following areas deserve additional scrutiny:

- repository and worktree isolation
- Git commit and push authorization guards
- runtime permissions and command execution
- cross-repository child-Herdr orchestration
- policy and rule enforcement
- dependency and completion gating
- prompt delivery and runtime settlement
- handling of local configuration, tokens, and credentials
- the Telegram Remote Operator adapter (remote input surface, bot
  token handling, approval binding)

Security fixes should include regression coverage whenever practical.

## Telegram Remote Operator surface

The Telegram adapter (`telegram_operator/`, `tgop`) is a
security-sensitive remote input surface. Its properties, all covered
by regression tests:

- **Outbound-only transport.** Genuine Bot API long polling; no
  webhook, no listener, no inbound port. The client socket deadline is
  a hard constant strictly greater than the server-side long-poll
  duration, so an idle poll is answered by the server, never aborted
  by the client; a deadline firing is handled as a normal empty poll.
  Failed polls recover with capped exponential backoff. These
  deadlines apply only to the Telegram transport — the Codex Gateway
  subprocess keeps its no-deadline behavior.
- **LaunchAgent KeepAlive limits.** KeepAlive restarts a process that
  exits; it cannot rescue a process that is alive but wedged. The
  restart throttle bounds relaunch frequency.
- **LaunchAgent executable resolution.** launchd does not inherit the
  interactive shell PATH, so the installed job's PATH is composed at
  install time from the directory the `codex` binary resolves to,
  placed FIRST so the validated binary cannot be shadowed by a
  different `codex` in a base directory, followed by a fixed
  hard-coded directory list — the ambient PATH is never passed
  through. Installation fails closed when `codex` is not resolvable,
  and the agent must be reinstalled if the binary moves.
  An explicit `--config` is propagated into the job absolutely;
  installing against a silently different default configuration is
  refused by construction.
- **Bot token handling.** The token sits in the URL path of every Bot
  API request, so every error, diagnostic, and exception string that
  can carry a URL is redacted before leaving the transport module. The
  config file must be mode `600` in a mode `700` directory; a
  group/other-readable config is refused, never repaired.
- **Authentication before content.** Every update is authenticated on
  its identity envelope (exact numeric user id, private chat, chat/user
  consistency) before any content is parsed or persisted. Unknown
  senders get no reply, and nothing they send is parsed or persisted —
  no content, intent, approval, work, or session state. The only
  durable effect of a denied update is the transport update-offset
  advance that stops the poller re-fetching it (intended, so a hostile
  update cannot wedge the poll loop).
- **Approval binding.** Plan approval is one-shot and bound to user,
  chat, repository realpath, gateway request, Codex session, plan
  message, exact plan digest, an adapter-held nonce, and an
  `expires_at` validity bound. No approval control exists before
  proof: the plan is sent with no keyboard, and the Approve/Reject
  buttons are attached to the bound plan message only after the send
  outcome proves the complete plan text was displayed and the message
  binding is durably persisted. A plan too long for the Telegram chunk
  cap is refused with no approval armed; truncated, partial, failed,
  or unverifiable delivery voids the approval with no buttons ever
  offered; and a failed or unverifiable button offer voids the
  approval too — an actionable approval never covers undisplayed
  text. The nonce never reaches the phone;
  callback buttons carry only an opaque id. Typed text containing the
  protocol marker is visibly quoted so a hand-typed decision envelope
  can never be accepted. Replay, mismatch, expiry, revision,
  duplicate-update, and crash-ambiguity paths all fail closed, and
  interrupted dispatches are reported and never replayed.
- **No delivery authority.** No remote message can commit, push, open
  a PR, tag, release, or deploy; the adapter's decision envelope says
  so explicitly. Delivery remains local and human-authorized.
- **Isolation.** Adapter and gateway sources never import, invoke, or
  reference the orchestration layer or its state directory — enforced
  statically (AST walk, token scan) and behaviorally (import probe and
  an audited end-to-end filesystem check).

## DI-REMOTE-2 Remote Target Repository Routing surface (unreleased)

DI-REMOTE-2 (implemented, **not yet part of any tagged release**)
lets an allowlisted Telegram user authorize one exact bounded mission
against a remote GitHub target repository while this repository stays
the permanent control and policy repository. Its security properties,
each behind regression and mutation coverage unless labelled
otherwise:

- **Durable workflow authority** (`workflow_authority/`). Every v2
  authorization lives in `workflows.json` beside the adapter config:
  a closed-schema record binding control identity and policy digest,
  canonical target, approved baseline, the per-field authority content
  (objective, constraints, rules, desired outcome, acceptance,
  unresolved questions, execution scope) and THE USER'S EXACT TYPED
  MESSAGE (`human_intent`), the exact rendered Mission Authorization and
  its digest, Telegram user/chat/message, nonce/expiry/consumption,
  handoff revision and digest, phases, workspace lease, receipts, Codex
  turn ids, ambiguity state, and `delivery_authority: "none"`. The
  exact human request is adapter-stamped (never supplied by the
  Operator — a document carrying it is refused), rendered verbatim
  (quoted) into the approved Mission Authorization text, bound by its
  own sha256, and therefore projected into every role-turn prompt via
  the rendered text — so the Operator can never change what the human
  said. Writes are atomic (temp file, fchmod
  600, fsync, replace, directory fsync) under a cross-process file
  lock; loads fail closed on any malformed, unknown-version, or
  unknown-key content and NEVER silently reinitialize. A store file
  that has become group/other-accessible is refused on load with an
  actionable `chmod` message — the store is authority-bearing, so an
  open mode is treated like a tampered file, never repaired silently.
- **Structure-only validation limit.** Mission Authorization
  validation validates STRUCTURE only: the closed key set, forbidden
  implementation-strategy keys (refused by normalized name, at any
  nesting depth), bounds, and exact bindings. It cannot read minds: a
  plan-shaped instruction can still arrive INSIDE a permitted field's
  free text (objective, constraints, handoff text). The protections
  around that residual are structural, not textual — the dispatch
  surface carries only `{target_repo, task, alias}`, so whatever the
  text says, the target Herdr Supervisor remains the first component
  that can turn it into an engineering plan, and no control-layer
  module can execute any of it.
- **One-shot v2 approval, and v1 can never authorize v2.** Approve
  Mission is bound to user, chat, control repository, exact rendered
  mission digest, workflow id, nonce, and a validity window, consumed
  exactly once, superseded by any newer mission in the chat. Two
  independent layers keep DI-REMOTE-1 approvals out: v2 consumption
  structurally requires fields a v1 approval does not have, and the
  explicit `tgop migrate-state` migration marks every pre-existing
  approval superseded for v2 purposes (missing or false marking fails
  closed). An existing schema-1 `state.json` fails closed at adapter
  startup until the human runs `tgop migrate-state`.
- **Delivery authority none, structurally.** The Runtime package
  contains no subprocess use outside its pinned seven-verb git
  transport seam, no `shell=True`, no environment reads, and no git
  delivery verb as a string value anywhere — enforced by AST and
  token scans over the whole package plus its entry script, and
  behaviorally by the adversarial matrix (refusals leave the control
  tree, workspace tree, and store byte-identical). No remote path
  can commit, push, open a PR, tag, release, deploy, or merge.
- **Read-only Codex role turns.** Every DI-REMOTE-2 role turn is a
  fresh process rooted at the control repository with
  `--sandbox read-only --ignore-user-config --ignore-rules
  --strict-config -c approval_policy=never` and no resume. The
  sandbox carve-out is value- and path-bound: only the exact token
  pair `("-s"|"--sandbox", "read-only")` is permitted, only from the
  role-turn builder, and `workspace-write`, `danger-full-access`,
  `--add-dir`, `--ephemeral`, `--approve-for-me`,
  `--skip-git-repo-check`, and every `--dangerously-*` flag stay
  banned unconditionally on every path. If the posture cannot be
  established unambiguously the turn is REFUSED — it never proceeds
  under ambient policy.
- **Codex sandbox telemetry limitation (A0), recorded verbatim.**
  During the accepted pre-implementation validation of the read-only
  sandbox posture:
  codex-cli 0.149.0 behaviorally denied both writes, but did not emit `command_execution` JSONL items for those denied attempts.
  Consequence: JSONL telemetry alone cannot be used as
  evidence that a denied write was attempted; the denial itself is
  behavioral. This limitation is preserved here independently of any
  test evidence, and `approval_policy=never` acceptance by the
  installed binary remains a human validation action item (probing
  `~/.codex` is forbidden by ruling).
- **Managed workspaces only.** Targets are cloned only after one-shot
  approval consumption, into a managed workspaces root, with
  containment checked before any git action (workspace ids are drawn
  from an alphabet in which path traversal is unrepresentable),
  canonical remote identity and approved baseline re-verified from
  the clone, dirty state refused, and cross-workflow lease reuse
  refused. The clone argv is pinned literally (no submodule
  recursion, no option injection).
- **Byte-exact dispatch, supervisor-first.** The dispatched task is
  the exact stored handoff text — the same string object end to end,
  proven by identity and byte-equality assertions — and the spawn
  request surface is pinned to exactly `{target_repo, task, alias}`.
  The initial dispatch is the BYTE-EXACT stored handoff; the separate
  corrective-follow-up path carries a bounded brief built from the
  authority fields.
- **Completion and the verified result.** After dispatch the Runtime
  observes the target through the existing read-only Herdr
  observability (`herdr.observe`); the stopped decision is driven by
  herd's own stopped set under SOURCE-SCOPED completeness (ruling
  R-6): a projection degraded in a CONSUMED source WAITS or stops
  durably (it never declares completion), while an agents-unprobed
  global PARTIAL — EXPECTED in production, since a dispatched target
  always has agents — weakens no consumed evidence. The raw global
  completeness is recorded and rendered unaltered, and the last
  distinct observation is surfaced in `/status`. A fresh
  verification turn then runs, but its `verified_result` is
  NECESSARY, NEVER SUFFICIENT: eight conjuncts (ten independent
  problem codes) are applied against a fresh disk read — the
  canonical target Reviewer APPROVE among them as TARGET-PRODUCED
  evidence that the target's own review process ran and concluded,
  never independent verification — and only then does DISPATCHED →
  VERIFIED → COMPLETED proceed, with the verified result returned to
  Telegram exactly once (reserve-before-send). Herdr lifecycle
  COMPLETE alone can never verify. No step grants delivery.
  "Exactly once" means never twice and never silently dropped — NOT
  that it always eventually arrives: if a send crashes between reserve
  and send (durable state `reserved`) or is only partly displayed
  (`partial`), the result is NOT re-sent automatically (the delivery
  pass selects only unreserved results), and `/status` says so
  ("not retried automatically, since …"); recovering it is a human
  step.
- **Dispatch-time protected-surface receipt.** Dispatch stamps a
  receipt (marker: `protected-surface baseline at dispatch`) binding
  the digest of this repository's protected surfaces at dispatch
  time; verification requires that receipt to exist and the surface
  digest to still match it. A workflow dispatched before the receipt
  existed FAILS CLOSED at verification — the baseline is never
  retro-fitted or fabricated.
- **Dispatch identity recovery, evidence-only (ruling R-3).** An
  identity-unresolved dispatched workflow is reconciled by binding
  EXACTLY ONE provable existing child — exact leased-workspace
  realpath match plus the lease's own observed task id, both
  observations source-scoped supported — or stopping durably
  BLOCKED. Reconciliation reads NOTHING outside this repository,
  never spawns, and the derived alias is a derived expectation only,
  never binding evidence. More BLOCKED outcomes are the accepted
  cost of that boundary.
- **Record-growth containment.** A workflow record at a hard bound
  (the Codex-turn cap included) stops that ONE workflow durably with
  a truthful capacity code (`broker_record_capacity_exhausted` /
  `runtime_codex_turn_capacity_exhausted`) and never kills the
  Runtime process.
- **Inherited-defect attribution.** The permanent-PARTIAL stall and
  the never-executable production role-turn wrapper corrected by
  this work were BOTH inherited from accepted task 20260826-022933;
  the record-growth containment class was found and closed across
  two increments of the correcting task (instance-wise, then
  structurally).
- **R-2, an authorization-scope bound (NOT a review-round limit).**
  Corrective follow-ups are bounded (2) as a bound on how far one
  human authorization may be stretched — it is explicitly NOT a
  review-round limit. Exceeding it transitions the workflow durably to
  NEEDS_REAUTHORIZATION (a fresh human mission is required), never a
  silent stop.
- **Hermetic-only caveat.** All of the above is proven with real
  local git fixtures, a real bridge-validation pass, injected
  transports/role-runners/spawns, and the read-only `herdr.observe`
  projection over local fixtures. These remain HUMAN validation items,
  NOT claimed to work live: the first live target dispatch, live child-
  Herdr spawn, live Telegram Bot API v2 traffic, live GitHub traffic,
  live Codex role turns, and the installed Codex binary's acceptance of
  the `approval_policy=never` config key.
- **Standing constraint on future work.** The capstone narrative's
  "the target Supervisor is the first strategy-bearing artifact" check
  covers only artifacts that exist BEFORE and AT dispatch (the mission,
  the byte-exact handoff, every control-chain prompt/context). It
  cannot catch a POST-dispatch strategy leak because no channel re-reads
  target files after dispatch today. If a future increment adds one
  (e.g. re-reading target content after dispatch), that enumeration
  MUST grow to cover it.
