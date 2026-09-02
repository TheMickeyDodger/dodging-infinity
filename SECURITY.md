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

## DI-REMOTE-2 Remote Target Repository Routing surface (v0.7.0)

DI-REMOTE-2, released in v0.7.0,
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
- **Completion and the verified result.** After dispatch the Runtime observes
  the target through the existing read-only Herdr observability. The fresh
  verification turn's `verified_result` is necessary, never sufficient: the
  complete durable verification conjunction must pass before
  `VERIFIED`/`COMPLETED`.

  Final-result delivery uses one dedicated bot-owned Telegram placeholder.
  Its exact chat/message identity is durably bound before consequential
  dispatch. After verification, the final result edits that same message
  object. Ambiguous placeholder creation fails closed before dispatch;
  crash-after-edit recovery reconciles against the same object; a
  placeholder-bound workflow can never fall back to a second result
  `sendMessage`; and an oversized final render fails closed rather than being
  chunked or truncated.

  Herdr lifecycle COMPLETE alone can never verify. No step grants Git,
  release, or deployment authority.

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
- **Automated/live evidence boundary.** Production exercised real Telegram
  v2 traffic, GitHub target materialization, child-Herdr bootstrap and
  dispatch, fresh installed-Codex role turns, target Herdr COMPLETE, and a
  canonical target Reviewer APPROVE on the historical Mitiq #2802 mountain.
  That particular workflow then exposed genuine post-dispatch policy drift
  and correctly terminated BLOCKED; `verified_result` and `result_delivery`
  remained null in that historical execution and no target Git delivery
  occurred.

  The corrected independent verification, `VERIFIED`/`COMPLETED`, and
  exactly-once final-result contract is certified by the later hermetic and
  adversarial v0.7.0 release evidence. A fresh post-fix live mountain is not
  used as release evidence. Separate artifact delivery and remote
  Git/release/deployment authority remain outside that certification.

- **Standing constraint on future work.** The capstone narrative's
  "the target Supervisor is the first strategy-bearing artifact" check
  covers only artifacts that exist BEFORE and AT dispatch (the mission,
  the byte-exact handoff, every control-chain prompt/context). It
  cannot catch a POST-dispatch strategy leak because no channel re-reads
  target files after dispatch today. If a future increment adds one
  (e.g. re-reading target content after dispatch), that enumeration
  MUST grow to cover it.

## Managed workspace trust (DI-REMOTE-2 I1, v0.7.0)

### The boundary in three parts

**WHAT IT DOES.** Trust establishment writes the single
`hasTrustDialogAccepted` key into one `projects` entry of the user-global
`~/.claude.json`, so a Herdr starting in a freshly materialized managed
workspace is not stopped by an interactive trust dialog no unattended run can
answer.

**THE EXACT SCOPE IT IS CONFINED TO.** One entry at one path: the
`workspace.lease_path(workspaces_root, workflow_id)` this workflow itself
materialized, under the DI-owned managed workspaces root. Outside that single
entry sit every other project entry, every top-level key, and every path this
workflow has not leased.

**HOW IT FAILS CLOSED OUTSIDE THAT SCOPE.** Within this module a path outside
the managed root, another workflow's lease, a subdirectory of this lease, a
symlink resolving out of the root, a traversing path, a non-directory, an
absent path, and a configuration DI does not account for each produce a
refusal that leaves the file byte-unchanged. The refusal is durable: it appends
a receipt whose summary begins `workspace trust not established`, names the
problem code, and moves the workflow to the terminal BLOCKED phase, from which
dispatch is unreachable.

**WHERE IT DOES NOT FAIL CLOSED — disclosed, and mitigated at the point of
use.** The CLI takes `<config>.lock` with `retries: 0` and, on contention,
falls back to an UNLOCKED read-modify-write, so a CLI write that begins in that
fallback can overwrite DI's entry after DI's read-back has already succeeded.
The consequence is a fail-OPEN — the workspace untrusted when the Herdr starts
— and it is bounded by re-verifying trust immediately before the spawn, against
the configuration the child will actually read: a workspace that is no longer
trusted refuses the dispatch durably. That check answers for that moment only.

A Herdr the Runtime starts in a freshly materialized managed
workspace is an interactive TTY session, and the Claude CLI gates the
first interactive start in an unseen directory behind a trust dialog
no unattended run can answer. The installed CLI exposes no `trust`
verb, no flag, and no settings key that grants it; its own diagnostics
name exactly one alternative, which is what DI does. This is a
security surface, and its blast radius is bounded by construction:

- **One key, one entry.** DI writes exactly one key,
  `hasTrustDialogAccepted`, into exactly one `projects` entry of the
  user-global `~/.claude.json`. Each other project entry and each
  top-level key is byte-identical before and after.
- **One path, derived not retyped.** The entry is only ever the exact
  `workspace.lease_path(workspaces_root, workflow_id)` this workflow
  itself materialized, under the DI-owned managed workspaces root.
- **Everything else is refused with no write.** A path outside the
  managed root, another workflow's lease, a subdirectory of this
  lease, a symlink resolving out of the root, a traversing path, a
  non-directory, and a path that does not exist each refuse with
  their own problem code and leave the file byte-unchanged.
- **No ancestor is written by this module.** Within it the managed
  root and its ancestors are refused, for two reasons of unequal strength.
  Unconditionally: writing a directory DI did not materialize is a
  change to user-global state outside this module's stated blast
  radius. As defence-in-depth: the CLI resolves trust by walking UP
  from the working directory, so an ancestor entry can confer trust
  on everything beneath it — which does **not** bite today, because
  that walk is bounded at the enclosing git root and a materialized
  workspace is its own git root (demonstrated by execution in this
  increment's evidence), but does bite the moment a workspace is
  materialized as a subdirectory of a repository.
- **No global weakening within this write.** In the write DI makes,
  no top-level key is added, changed or removed, and no
  `allowedTools` or other permission surface is widened — neither in
  DI's own entry nor in another's. Outside that write, what other
  processes do to the file is not covered.
- **A config DI does not account for is left alone.** Within this
  module each of the following is a refusal rather than a repair, a
  re-creation or a rewrite from scratch: missing, unreadable,
  unparsable, a non-object root, a missing `projects` object, a
  `projects` that is not an object, and an existing entry that is not
  an object.
- **Atomic write.** The new content goes to a temp file in the same
  directory and is moved into place with a rename, so a concurrent
  reader observes the whole old file or the whole new one, and an
  interrupted write leaves the original byte-for-byte intact.
- **The CLI's own lock.** The read-modify-write runs while holding
  `<config>.lock`, the same `mkdir` lock directory the CLI itself
  takes. Within this module a lock held by another writer is not
  reclaimed or broken — sustained contention is a refusal, not a
  forced write — and a lock DI holds is not heartbeated, so a DI
  process that dies is reclaimed by the CLI's own staleness rule
  instead of wedging the user's sessions. Outside that boundary, and
  disclosed: a lock older than the CLI's own staleness window is
  reclaimed once, and two DI processes seeing the same stale lock
  have a small unguarded window.
- **Disclosed residual: lost updates are bounded, not eliminated.**
  The CLI acquires that lock with `retries: 0` and, on contention,
  falls back to an UNLOCKED read-modify-write, so a CLI write that
  begins in that fallback can still overwrite DI's entry. The
  read-back below detects a write that did not survive to disk;
  outside that moment it does not detect a later clobber. Left there,
  the CONSEQUENCE would be
  a fail-OPEN — the workspace untrusted at Herdr start, the Herdr
  stopped at a dialog no unattended run can answer — which is the
  exact failure this mechanism exists to prevent. It is therefore not
  left there: trust is re-verified at the POINT OF USE, immediately
  before the spawn, and a workspace that is no longer trusted refuses
  the dispatch durably instead of starting a Herdr that would hang.
- **Vendor facts are pinned to a CLI version.** Every statement above
  about what the CLI itself does — the `projects` trust key, the
  upward walk, `<config>.lock`, `retries: 0`, the 10s staleness
  window, `realpath: true` — was derived from **`claude 2.1.251`**
  and is true of that version, and of no other. Within that version
  the real-CLI arms drive the actual binary and fail loudly on a
  change to the gate; outside it — for the derived vendor constants
  (`retries: 0`, the staleness window, `realpath: true`), which no
  test observes the CLI performing. Outside the arms' reach a change
  to those would go quietly false, so re-derive them whenever the
  installed CLI moves.
- **The configuration path is resolved before use.** A
  `~/.claude.json` that is a symlink (a dotfiles checkout) keeps its
  identity: within this module's own writes, DI locks, writes, and
  reads back the RESOLVED path, so the symlink is not replaced by a
  regular file and DI takes the same lock the CLI does. Outside that
  boundary, and disclosed: another writer that renames the link path
  is not covered.
- **Trust is established within the boundary where it can be
  consumed.** If the
  configuration DI wrote is not the one the Herdr this dispatch would
  start will actually read, the dispatch is refused durably rather
  than reporting success for an effect that no component would
  consume. The check covers that moment only.
- **Read-back decides success.** The answer comes from a fresh read
  of the file from disk rather than the in-memory document; a
  mismatch is a refusal. That read answers for the file at that
  moment, and not for its later state.
- **Refusal is durable and actionable.** A failed establishment
  appends a reason receipt naming the problem code and moves the
  workflow to the terminal BLOCKED phase — within the phase machine
  that is not a silent retry, not a fallback to an interactive
  prompt, and not a step toward dispatch, which becomes unreachable.
- **Idempotent.** Within this module, re-establishment on an entry
  that already records trust performs no write.
- **The grant is REVOKED at release (I5-1).** `workspace_trust.revoke`
  removes exactly the entry this workflow established, at exactly its
  own lease realpath, under the same discipline as establishment:
  atomic, lock-held, post-write read-back, a corrupt configuration is
  a refusal, and a failure is a durable actionable receipt. The
  release path calls it BEFORE removing the workspace directory, and
  `revoke` does not require that directory to exist — so a crash
  between the two steps leaves the entry removable rather than
  stranded, which is the condition that produced the observed orphan.
  Revocation is idempotent, so a repeat is correct rather than an
  error.

  The boundary, stated with the guarantee: revocation reaches ONE
  `projects` key, the one whose path equals this workflow's own
  derived lease path inside the managed root. Outside that reach sit
  another project's entry, an entry at a path this workflow has not
  leased, and every top-level key — and `tests/test_ownership.py`
  proves the boundary by comparing the surrounding configuration as
  serialized text rather than by inspection.

  Outside the revocation path, and disclosed: an entry written by an
  earlier build that had no revocation is not removed retroactively
  by this change; it is removed the next time that workflow reaches
  release with its lease still recorded, and an entry whose workflow
  record is gone entirely has no owner able to prove it and is
  therefore left alone.
