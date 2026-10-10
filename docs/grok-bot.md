# Grok Bot transport

`grok_bot` is a thin transport adapter between a Grok Bot conversation and
Dodging Infinity. It decides nothing.

- A plain-text request goes to the Codex Outer Operator, which authors the
  Mission proposal.
- Presentation, approval, status and the run commands belong to the local
  request surface (`local_request`).
- A separate delivery approval belongs to `pr_delivery`'s own ceremony.

Every result carries `delivery_authority: "none"`, meaning this transport
itself grants nothing. The adapter has no tool that performs a commit, push or
pull request, and no merge, release or deploy tool.

## Three limitations, preserved

1. **Approval is operator-attested, not cryptographically authenticated.**
   This applies to Mission approval and delivery approval alike. Grok Bot gives
   DI no signed sender attribution, so DI does not establish who sent a reply.
2. **Loopback tests prove protocol shape only.** They never prove live Grok
   Bot interoperability; see the live compatibility dependency below.
3. **The `2025-03-26` batch gap.** If the live Grok Bot client negotiates only
   MCP `2025-03-26`, it cannot use this endpoint (see
   `mcp_2025_03_26_batch_reception` below).

## Tools

| Tool | What it does |
|---|---|
| `request` | Sends the human's plain text to the Outer Operator, which proposes a Mission or asks a question. |
| `present` | Returns the exact display text, the binding the human approves and the exact local `arming` command, and durably records what was displayed. |
| `approve` | Relays a separate reply whose whole text is `approved`, with the `approval_code` the local arming printed. Its binding must equal the displayed one in every field, and the code must fire the approval armed for exactly that binding, once. |
| `status` | Durable status and run, read from DI's records. |
| `recover` | Rebinds a request whose proposal step was interrupted. |
| `cancel` | Withdraws the caller's own pending proposal using its one-shot control capability or, if the `request` reply was lost, the request's exact `text` and `conversation_ref` (see [Cancelling after a lost `request` reply](#cancelling-after-a-lost-request-reply)). |
| `run` | Drives the request's own authorized Mission: `dispatch`, `observe`, `reconcile`, `prove`, `verify`, `result`, `pause`, `resume`, `cancel`. Each command's exact arguments are listed below. |
| `present_delivery` | The separate delivery: `pr_delivery present-dots` proposes exactly `BASE_REFRESH`, `COMMIT`, `PUSH`, `PR_CREATE` for the live candidate. It displays the complete proposal, returns its local `arming` command, and records what was displayed. |
| `approve_delivery` | Relays the human's separate `approved` reply, with the `approval_code` of the delivery's local arming, to `pr_delivery attest-dots`, for exactly the displayed delivery. |
| `delivery_status` | A delivery's status. It loads `pr_delivery`'s record first, then checks the identity of the repository that record names (reading that repository's `.git` pointer files), then projects the status. It runs no Git, opens no network connection and performs no step. |

Approval is **operator-attested, not cryptographically authenticated**. Grok
Bot gives DI no signed sender attribution, so DI does not establish who sent
the reply.

### Local arming: the approval boundary

Holding the MCP bearer token is not enough to approve anything. Before
`approve` (or `approve_delivery`) can succeed, the human **arms** the approval
by a local action on the DI machine:

1. `present` (or `present_delivery`) returns `arming`: one exact local command,
   `grokbot.py … authorize` (or `authorize-delivery`). It carries the **full**
   displayed binding: request, Mission id, revision, all 64 hex characters of
   the proposal digest, the action scope, the delivery targets, the expiry and
   the digest of the displayed text. Nothing in it is shortened.
2. Grok Bot runs that command in its per-command, user-approved local shell
   on the DI machine (a vendor capability that must be enabled; see the
   vendor references below). Under the default "Ask every time" policy the
   human approves that exact command string, so their consent is to the full
   binding.
3. Before anything takes effect, and inside the same critical section
   `present` and `approve` use, the command refuses (by name,
   `grok_bot_arming_not_displayed`) unless every value equals the **latest**
   presentation, the proposal is still that one, and its display re-renders to
   the recorded digest. A presentation made in between cannot inherit the
   arming, nor the arming fire against a newer presentation. An expired
   display is refused (`grok_bot_arming_expired`).
4. It then stores only a one-way commitment (SHA-256 over the binding and a
   128-bit random nonce) and prints the nonce once as the `approval_code`.
   DI never stores the code.
5. `approve` must carry that code. It fires the armed approval exactly once
   (`grok_bot_approval_consumed` after), only for exactly the armed binding
   (`grok_bot_approval_code_mismatch` otherwise; five wrong codes kill it),
   and never without an arming (`grok_bot_approval_not_armed`). Consumption is
   durable before anything is applied.

**What a code can do depends on its kind:**

- **A Mission code** (from `authorize`) confers NO delivery authority.
- **A delivery code** (from `authorize-delivery`) redeems EXACTLY ONE locally
  armed delivery authorization. Presented to `approve_delivery` with the
  displayed binding (public values) over the endpoint (bearer access), it has
  pr_delivery's ceremony record one PR Delivery Authorization, for exactly
  that displayed delivery. The ceremony's remaining checks still apply: the
  live candidate, expiry at application, and one authorization per proposal.
- **Neither kind** is a principal or a standing credential, and neither
  confers general or standing Git authority.

What carries this, stated precisely:

- **Hash-only persistence.** The commitments file holds the commitment and
  metadata, never the nonce or the code, so read access to anything DI stores
  yields nothing that fires.
- **A dependency, not a property of the scheme:** the `request` tool's Operator
  runs under the pinned fail-closed read-only sandbox posture, which is what
  is relied on to stop it writing the commitments file or running the arming
  command itself. DI cannot enforce the provider's sandbox. If that
  confinement does not hold on some host or provider version, a
  request-controlled Operator could arm an approval, and this separation
  fails.
- **Local arming is the boundary; the code relay is not.** A code persisted
  by the local shell's output somewhere readable (terminal saved state, for
  example) fires only the exact binding the human armed, once, before its
  expiry: it completes the human's own authorization and creates no new one.
- **A workflow guardrail, not an enforced boundary.** Like the Git gates,
  local arming is not designed to contain processes running with the user's
  own privileges, a Herdr worker included. DI cannot see or enforce the
  vendor's local-shell approval policy: under "Always allow" no per-command
  human approval occurs. No part of this is cryptographic human
  authentication.

`present` shows the complete proposal: nothing is truncated or elided. Every
field of the recorded proposal, including the whole proof contract, appears
in `display_text`. Over MCP, `display_text` is the first text content of
`present`'s result, whole.

### `run` commands and their arguments

`run` takes `request_ref`, `command` and an `arguments` object. The
`arguments` object must hold exactly these names, with these types, and
may add only the optional ones listed:

| Command | Arguments | Notes |
|---|---|---|
| `dispatch` | none; `workspace_path` (string, optional, operator recovery only) | No path is needed: DI prepares the Mission's own isolated workspace (see [Automatic Mission workspaces](#automatic-mission-workspaces)). `workspace_path` is operator recovery only: it may name that same, already prepared workspace and nothing else. |
| `observe` | none | |
| `reconcile` | none | Resolves an unknown dispatch outcome; see below. |
| `prove` | `operation` (string, required), `arguments` (object nesting at most 14 deep, required) | One existing Mission State proof seam, with exactly that seam's own arguments. |
| `verify` | `reported_result` (any JSON value nesting at most 14 deep, required) | DI checks it against its own fresh reads. |
| `result` | none | |
| `pause` | none | |
| `resume` | none | |
| `cancel` | none | |

- For a command marked none, omit `arguments` or send `{}` or `null`.
- Any other name, a missing name or a wrong type is refused
  (`grok_bot_bad_request`).
- `tools/list` states the same contract per command: a `oneOf` over
  `command`, derived from the adapter's own check (`RUN_ARGUMENTS`, and
  `RUN_OPTIONAL_ARGUMENTS` for the optional recovery override).
- It states the adapter's nesting bound too. The adapter refuses any call
  that nests lists or objects more than 16 deep (`MAX_NESTING_DEPTH`),
  counting the call itself and its `arguments` object. That leaves an
  argument value 14 levels, which the schema expresses with depth-bounded
  `$defs` derived from the same constant.
- The schema and the adapter are checked to accept exactly the same calls,
  including both sides of that boundary.

**`reconcile` and a late child.** A dispatch whose outcome was lost may still
have started a child, possibly late.

- If the workspace cleanly shows no task record yet, `reconcile` records an
  evidence-only attempt and binds, spawns and re-dispatches nothing. Call it
  again later.
- Degraded or malformed observation evidence is not "not yet": an
  unreadable, malformed or unsupported observation, or a task record without
  a usable id, still stops the run at once (`reconcile_degraded`).
- At most 16 such attempts are allowed (`MAX_RECONCILE_ATTEMPTS`). The 16th
  stops the run durably (`reconcile_not_observable`).
- A late child is adopted only on the full proof:
  - its own task record names this Mission's dispatch;
  - the control repository's spawn listing is clean and complete;
  - exactly one listed child names the workspace, with the workspace's own
    task id.
- Anything else stops the run durably, each with its own reason.
- A proven late child whose own task record says `ABORTED` stops the run
  (`reconcile_late_child_aborted`). It is never observed `RUNNING`, verified
  or written to. The child's status is read from its own task record, never
  from the parent's spawn listing, which can be stale.
- `BLOCKED` stays terminal. For a run stopped because nothing was observable
  yet (including the `reconcile_degraded` stop the first reconcile recorded
  before this change), `reconcile` records one durable, evidence-only late
  resolution: the proven late child and its own status. The Mission stays
  `BLOCKED`, nothing is adopted or resumed, and continuing needs a new
  Mission.

## Automatic Mission workspaces

An ordinary Grok Bot conversation dispatches an approved Mission **without
any path**: `run` `dispatch` takes no arguments. DI prepares one isolated Git
worktree for the Mission and dispatches Herdr there.

**Configure once, on the DI machine.** Give the endpoint (or the `run`
command line) two absolute paths:

- `--workspace-repository R`: a local checkout of the approved repository.
  Its `origin` must canonicalize to the repository the Mission names.
- `--workspaces-root W`: an existing, writable directory outside every
  repository. No directory from `W` up to `/` may hold a `.git` entry or be
  a Git directory. So a root inside the configured repository, another of
  its worktrees, the control repository, a third repository or a bare
  repository is refused before anything is bound or created. If your home
  directory is itself a Git repository (dotfiles, say), no root under it
  qualifies: choose one outside it. It is never created for you.

Without both, every dispatch is refused as unavailable (see below). Nothing
is read from the environment and no default path is assumed.

**What a dispatch does.**

1. It derives the path `W/MISSION_ID` from the Mission id alone. The same
   Mission always gets the same path, and two Missions never share one.
2. It records the binding durably in Mission Core as `preparing` before
   anything exists at that path. The binding covers the Mission, its
   revision and proposal digest, the approved repository, the configured
   repository, the path, and the baseline: the configured repository's
   `HEAD` commit, observed by DI and never human-approved.
3. Under an exclusive, non-blocking lock in `W`, it creates the worktree with
   `git worktree add --detach --lock --reason MARKER`. The worktree is
   detached at the baseline and locked from its first moment, so `git
   worktree prune` never removes it. Repository hooks do not run. The lock
   reason is DI's marker, naming the Mission and the digest of its exact
   binding.
4. It verifies the worktree. Then it establishes Claude workspace trust for
   exactly that path. A fresh directory would otherwise stop the started
   Herdr at the CLI's trust dialog, which no unattended run can answer.
   This uses the same narrowly scoped mechanism the Runtime uses for its
   own managed workspaces (`target_runtime.workspace_trust` through
   `RuntimeWorker`): one `hasTrustDialogAccepted` key in one `projects`
   entry, only for this Mission's own path under the root. Only then does
   it record the binding as `prepared`.
5. Immediately before the run intent, it re-checks the worktree: Git
   identity, cleanliness, no Herdr task or runtime record, and trust
   present in the configuration the started Herdr will actually read.
6. It records the intent, which must name exactly the prepared path and
   baseline. Mission Core refuses any other intent. Only then is Herdr
   started.

`status` shows the binding (`workspace`) and the latest refused preparation
(`latest_workspace_refusal`), both read from durable records.

**Exact reuse only.** A later dispatch of the same Mission, for example
after a restart, reuses the worktree only if every one of these holds:

- the path is a real directory, not a symlink;
- the configured repository records exactly one worktree there;
- its lock reason is exactly this binding's marker;
- its worktree identity is reciprocal: exactly one administrative
  directory of the configured repository points back at this checkout,
  and the checkout's own `.git` pointer, a regular file and not a symlink,
  resolves to exactly that directory. A checkout repointed at another
  worktree's metadata could otherwise share that worktree's `HEAD` and
  index;
- its Git common directory is the configured repository's;
- it is detached at the recorded baseline;
- it is clean;
- it holds no `.herd/state/task.json` or `runtime.json`.

**Refusals.** Each refusal is truthful and durable. It is recorded as the
Mission's latest workspace refusal, with a count. No refusal records an
intent or starts anything, and nothing is ever repaired, reset, pruned or
deleted. What a refusal leaves behind depends on when it happens:

- **Before the binding** (unavailable repository, root or trust
  configuration, collision, foreign, conflicting, active): nothing is
  created or bound.
- **After the binding, before a worktree exists** (busy, or a Git add that
  failed before creating anything): the `preparing` binding remains, with
  nothing at its path.
- **After the worktree was created** (a Git add whose outcome was not
  confirmed, a failed post-creation check, or a trust-establishment
  failure): that new worktree is kept, with its `preparing` binding. The
  next dispatch adopts it only if it is exact.
- **At an existing worktree** (dirty, moved, Herdr state, metadata
  identity): the worktree is kept exactly as it is.

| Problem | When |
|---|---|
| `mission_bridge_repository_unavailable` | The repository is not configured, is missing or unreadable, or is not the approved repository. |
| `mission_bridge_workspace_root_unavailable` | The root is not configured, missing, not writable, or inside a repository (any directory from the root up holds `.git` or is a Git directory). |
| `mission_bridge_workspace_collision` | An unrelated file or directory, empty or not, is at the derived path. |
| `mission_bridge_workspace_foreign` | A checkout DI did not prepare for this binding is at the path: no marker or another one, a symlink, another repository's checkout, a `.git` pointer repointed at other metadata, metadata that does not point back, a foreign common directory, or a `.git` or administrative `gitdir` pointer that is malformed (an embedded NUL, not UTF-8, not exactly one line, no target) or cannot be read or resolved. Also refused: an exact-looking worktree when the Mission holds no durable binding. Ownership is never inferred from a path or a marker. |
| `mission_bridge_workspace_conflict` | The binding and the disk disagree: another `HEAD`, a recorded worktree that is gone, another configured repository, or another Mission's terminal workspace (kept as its evidence). Also used when a workspace holds only a stopped Herdr task's record. |
| `mission_bridge_workspace_active` | The path is bound to another Mission's live run, or the worktree holds a Herdr task or runtime record this Mission's intent does not represent. `.herd` is ignored by Git, so a clean worktree does not prove it inactive. |
| `mission_bridge_workspace_not_clean` | The worktree has uncommitted content. |
| `mission_bridge_workspace_trust` | Workspace trust is not configured (refused before anything is created), could not be established (the problem code is named; the newly created worktree and its `preparing` binding are kept), or is not present at the point of use in the configuration the started Herdr reads (the prepared worktree is kept). Nothing starts. |
| `mission_bridge_workspace_busy` | Another preparation holds the root's lock. It is not queued; retry. |
| `mission_bridge_workspace_preparation_failed` | Git did not confirm the worktree. The next attempt inspects what is there and adopts only an exact worktree. |

**Retries, concurrency and restarts.**

- A crash at any point leaves one of four things: nothing; a `preparing`
  binding with nothing at the path; DI's exact worktree; or, from a crash
  inside `git worktree add`, a partial directory. The next dispatch
  reconciles the first three to a single worktree, adopting only an exact
  one. A partial directory is never exact, so it is refused (collision,
  foreign, conflict or not clean, depending on what it holds) and kept as
  it is: never adopted, completed or deleted. Recovering from it is a human
  step.
- Overlapping preparations never make two worktrees: one wins the lock, and
  the other is refused as busy or adopts the finished worktree.
- One intent per Mission still admits exactly one dispatch.
- After an uncertain dispatch, nothing is redispatched. The run stays a HOLD
  that only `reconcile` resolves, exactly as before.

**Evidence is kept.** Completed, paused, cancelled and blocked worktrees stay
where they are, still locked. Another Mission never binds them, and this
change has no cleanup.

**Operator recovery only: `workspace_path`.** `dispatch` accepts one optional
argument, `workspace_path`, for an operator recovering a dispatch. It is
never needed in a conversation, and it is not a bypass:

- the configuration must still be present;
- the path must be exactly this Mission's own derived workspace, so any
  other path (an unrelated checkout, another Mission's worktree, anything
  else under the root) is refused as `mission_bridge_workspace_foreign`;
- the Mission must already hold its durable binding;
- it never creates a workspace or a binding;
- every check above applies unchanged.

What it recovers is an uncertain preparation acknowledgement, for example a
crash after the worktree was created but before it was recorded `prepared`.
Losing the durable binding itself is not recoverable this way. Authority is
never rebuilt from a path, a marker, a clean tree or a matching origin.

On the command line, `direquest.py dispatch` takes the same two flags, and
`--workspace P` is the same operator recovery path.

## Cancelling after a lost `request` reply

The control capability a `request` returns is shown in that reply only. If
the reply is lost (for example, to a client timeout), the conversation can
still withdraw its own pending proposal:

1. Repeat the identical `request` (same `text`, same `conversation_ref`).
   It answers with the existing `request_ref` (`duplicate: true`). Nothing
   is proposed again.
2. Call `cancel` with that `request_ref` and the same `text` and
   `conversation_ref`, instead of `control_capability`. Never send both.

How it works, and what it does not do:

- **A `conversation_ref` is required, and it must be a secret.** Recovery is
  kept only for a request that carried one. Use a private, unguessable,
  random value made for the conversation: a long random string, never a
  visible or sequential identifier such as a chat or message id. Uniqueness
  alone is not enough. The exact text alone could be restated from the
  proposal, which any caller can read.
- **The capability is sealed.** At `request` time the adapter seals the
  capability under the request's `text` and `conversation_ref`, in the same
  write that records the request. The index file therefore holds no
  capability usable without the origin proof, and it is only as safe as that
  proof. With a weak (visible, sequential or guessable) `conversation_ref`,
  anyone who also has the text can recover the capability.
- **The two values are not equally private.**
  - The `text` is sent to the Operator and may appear verbatim in the
    recorded proposal and in `present` output.
  - The `conversation_ref` is never sent to the Operator, displayed or
    returned, and is stored only inside a digest. No error message names
    that digest.
  - The protection therefore rests on the `conversation_ref` staying private
    and unguessable.
- **The surface's checks are unchanged.** The unsealed capability goes to
  the surface's own `cancel`, with its existing checks and refusal codes. It
  works only while the proposal is still awaiting a decision, and only
  within the capability's seven-day validity. A decided, edited, authorized
  or running Mission stays out of scope.
- **One proof, one request.** A proof cancels only the request it created.
  A `request_ref` or Mission id alone never cancels anything.
- **No capability is returned.** No response returns a control capability
  except the `request` reply itself.
- **This is not authentication.** It shows only that the caller knows the
  request's exact text and `conversation_ref`. Keep the `conversation_ref`
  private and unguessable.

Refusals:

| Code | Meaning |
|---|---|
| `grok_bot_cancel_origin_mismatch` | The text and `conversation_ref` do not name this `request_ref`. |
| `grok_bot_cancel_recovery_interrupted` | Local persistence was interrupted. `condition` says which: `in_flight` (the request's index entry was never completed), `not_indexed` (the surface holds a request this adapter has no entry for) or `local_cancellation_incomplete` (an earlier cancel withdrew the proposal in Mission Core but its local save failed; run cancel again with the same control capability). |
| `grok_bot_cancel_recovery_unavailable` | The request holds no recovery material: it was recorded before this change, or without a `conversation_ref`. The refusal reports the Mission's current state as Mission Core reads it (`mission_state`). |

When the request does hold recovery material, a cancel that earlier withdrew
the proposal in Mission Core but failed its local save is completed by the
recovery itself (`completed_interrupted_cancellation: true`).

**Known limits.**

- **Older requests stay uncancellable.** Pending requests recorded before
  this change hold no recovery material. That includes the live request
  `lr-8eb8c243004eb1434a1101ecb565ed96` (Mission
  `mn-4e5b7eb6b74c8564f78bc57d06cf22d1`). If such a request's reply was lost,
  it cannot be cancelled through this transport. While its Mission is still
  `AWAITING_DECISION` (as that live one was when last read), it can still be
  presented and approved.
- **A crash can still lose recovery.** If the process stops between the
  surface recording a proposal and the adapter recording it, no recovery
  material exists (`in_flight`).

## The delivery ceremony (separate from engineering approval)

Committing, pushing and opening a pull request need their own exact proposal
and approval. These are pr_delivery's existing `present-dots` and
`attest-dots`, relayed unchanged:

1. **`present_delivery`** passes `present-dots`' own arguments through.
   - `present-dots` reads the live repository and binds:
     - the candidate, by its identity digest and every entry;
     - the Herdr COMPLETE, Reviewer APPROVE and verification evidence;
     - the remote, branches and baseline;
     - the pull-request text;
     - the closed action set `BASE_REFRESH`, `COMMIT`, `PUSH`, `PR_CREATE`;
     - an absolute expiry.
   - `objective`, `architecture_notes` and `nonblocking_risks` must be literal
     text. An `@file` reference is refused, so no local file is read into the
     display.
   - `validity_seconds` must lie within pr_delivery's own authorization span,
     1 to 604800 seconds. `verification_ran_at` must be a number
     `present-dots` could hold: a finite float, or an integer no larger than
     the largest finite float. Anything else is refused as a bad request
     before the repository is read.
   - The display lists every candidate entry and every evidence field.
     pr_delivery's own summary stops after 50 entries; this display does not,
     and nothing is truncated.
   - The display, keyed by the proposal's own digest, is durably recorded
     before it is returned.
2. **The human arms it locally** with `present_delivery`'s `arming` command
   (`authorize-delivery`, carrying the full digest, the expiry, the display
   digest and the repository), which prints a one-time `approval_code` (see
   [Local arming](#local-arming-the-approval-boundary)).
3. **The human replies in a separate message** containing only `approved`.
4. **`approve_delivery`** relays that reply with the displayed binding (the
   proposal digest and expiry) and the `approval_code`, which must fire the
   delivery armed for exactly that proposal, once.
   - A digest never displayed here is refused (`grok_bot_not_presented`).
   - A differing expiry is refused (`grok_bot_binding_not_displayed`).
   - Nothing is corrected or filled in.
   - `attest-dots` then checks the whole-reply affirmative, the digest link,
     the expiry and the live candidate again, and records one
     operator-attested (`dots_operator_attested`) PR Delivery Authorization.
     It never records `local_terminal`.

**Updating an existing open pull request (`pr_update`).** `present_delivery`
also accepts `pr_number` and `head_branch`, both or neither: present-dots'
own `--pr-number` and `--head-branch`.
- The proposal then authorizes exactly `COMMIT` and `PUSH`: ONE new commit
  on that pull request's head branch, a strict fast-forward of its head. It
  never refreshes a base and never opens a pull request.
- Above the complete proposal, the display names:
  - the pull request number and its head and base branches;
  - the expected head SHA;
  - the staged hash (sha256 of `git diff --cached --binary` against that
    head, re-checked before the commit and by the git hook);
  - the candidate identity, a separate binding;
  - the `prd-` delivery id that approval will mint;
  - exactly the authorized steps, read from the proposal itself.
- The display never claims `PR_CREATE` or `BASE_REFRESH`. The grant reports
  the record's own steps (`COMMIT`, `PUSH`), with `performed_steps` empty.
- A closed, merged, moved or mismatched pull request is refused by name.
  Nothing is recorded.
- This transport still mints nothing and performs no step. The checkout
  prerequisites differ for this kind:
  - HEAD is on the named head branch, at the pull request's head, and the
    remote head ref equals it;
  - the read adds `gh pr view` for that one pull request;
  - unrelated unstaged or untracked paths may remain, if disjoint from every
    candidate path.

**Ceremony prerequisites and effects.** `present_delivery` and
`approve_delivery` each run pr_delivery's live-repository read on the DI
machine, through its real git transport.

- **The checkout (`repo`) must have:**
  - HEAD on a named branch, directly on the base branch;
  - the remote configured, with a URL that resolves;
  - the base branch present on the remote;
  - the working tree exactly the staged candidate;
  - `user.name` and `user.email` set.
- **What the read runs:**
  - local reads, such as `git status --no-optional-locks`, `diff-index` and
    `config`;
  - `git ls-remote --exit-code <remote> <base>` against the configured remote;
  - when the remote base differs from HEAD,
    `git fetch --quiet --no-tags <remote> <base>`.
- **Effects:**
  - With a real remote such as GitHub, `ls-remote` and `fetch` connect to that
    external host, using the machine's own git configuration and credentials.
  - The fetch writes local repository data (objects and refs).
  - The adapter's own code opens no connection, and it does not suppress the
    ceremony's.
  - `delivery_status` runs no ceremony and no Git. It reads pr_delivery's
    store for the delivery's record, plus the `.git` pointer files of the
    repository that record names (the identity check below). It opens no
    network connection and performs no delivery step.
  - The Grok Bot adapter tests replace the transport with a recording double,
    so no git process runs any of these.

What this does **not** do:

- An engineering approval never authorizes delivery.
- Approving a delivery performs no delivery step: this transport runs none of
  `BASE_REFRESH`, `COMMIT`, `PUSH` or `PR_CREATE`. The approval's ceremony still
  re-reads the live repository, including the configured remote (above).
- The authorized steps run only through pr_delivery's own drive, which a
  human runs outside this adapter and no Grok Bot tool calls (see
  [Completing a delivery](#completing-a-delivery-human-run-outside-this-transport)).
- Merge, auto-merge, tag, release, deploy, publish and force push are never
  authorized.
- `grok_bot/delivery.py` is the only module outside `pr_delivery/` and the
  git guard that imports `pr_delivery`. It never mints an authorization, and
  never advances or revokes a step.
- It never constructs a delivery transport, machine or store **directly**.
  - Construction is core-owned, by pr_delivery's own `build_machine()`.
    `pr_delivery/cli.py` is the only production site that constructs the real
    zero-argument `DeliveryTransport()`.
  - That runs inside the ceremony's `present_dots_cmd` and `attest_dots_cmd`.
  - Called from `grok_bot`, it is used solely for the read-only status
    projection, never to advance a step.

### Completing a delivery (human-run, outside this transport)

This section documents the existing, supported continuation. This transport
never runs it, and none of the Grok Bot adapter tests runs it. pr_delivery's
own baseline fixtures (`tests/test_pr_delivery.py` and its siblings) do
perform real delivery effects in temporary repositories.

**Three states, never conflated.**

| State | What it means | What it is not |
|---|---|---|
| **Verified** | The engineering result is verified (for a Mission, `verify` reached VERIFIED). `present-dots` binds its Herdr COMPLETE, Reviewer APPROVE and independent verification evidence into the proposal. | It is not an authorization. It authorizes no delivery. |
| **Authorized** | `approve_delivery` succeeded. pr_delivery recorded one PR Delivery Authorization in phase `AUTHORIZED`, every authorized step `pending`. | It is not a delivery. Nothing is committed, pushed or opened yet. |
| **Delivered** | A human ran pr_delivery's own drive and its steps succeeded: the record reached `COMPLETE`. A Mission's `status` reports `delivered` only from attested P1-A6 receipts, and only for a delivery whose parent is that Mission. | It is not a merge. Merging is never authorized here. |

An `AUTHORIZED` record must never be read as delivered. `delivery_status` (or
`python3 -m pr_delivery status`) shows the phase, and only `COMPLETE` means
the steps ran.

**What the human runs.** On the DI machine, from the Dodging Infinity
checkout (as for `present-dots`), with the `delivery_id` that
`approve_delivery` returned:

```
python3 -m pr_delivery status  --delivery-id prd-…
python3 -m pr_delivery advance --delivery-id prd-…
```

- `advance` runs the authorized steps in order: `BASE_REFRESH`, `COMMIT`,
  `PUSH`, `PR_CREATE`. It goes through pr_delivery's own transport. For a
  `pr_update` delivery it runs `COMMIT` and `PUSH` only, then one effect-free
  read of the pull request before `COMPLETE`.
- It stops when the record is `COMPLETE` or `BLOCKED`, or when a retryable
  failure is recorded. An expired authorization blocks.
- During `advance`, the installed Herdr git guards accept each commit and
  push on the one-shot receipt derived from this exact authorization. That is
  a second path beside the manual `approve-commit` / `approve-push` tokens,
  not a bypass of the guards.
- To withdraw the authorization instead:
  `python3 -m pr_delivery revoke --delivery-id prd-… --reason "…"`.

**The deterministic human gates are retained.**

- **Minting.** An authorization is minted only by pr_delivery's two existing
  ceremonies, through its single minting site:
  - the local-terminal `authorize` ceremony, with its TTY confirmation (source
    `local_terminal`);
  - `present-dots` then `attest-dots` (source `dots_operator_attested`).
- **Operator-attested source.** `dots_operator_attested` is operator-attested,
  not independently verified, and never records `local_terminal`.
- **Engineering approval.** An engineering approval alone never produces an
  authorization.
- **Transport construction.** `pr_delivery/cli.py` (`build_machine()`) is the
  only production site that constructs the real zero-argument
  `DeliveryTransport()`. `grok_bot` constructs none directly.
- **Never authorized:** merge, auto-merge, tag, release, deploy, publish and
  force push, by either ceremony.

**Tests.** The delivery tests run pr_delivery's real ceremony over a recording
double for the whole transport, git half included. Every process-starting
seam is replaced by a recorder that raises and is asserted unreached, so no
git or `gh` process starts and no step is performed.

## Running the endpoint locally

```
grokbot.py --state-dir /abs/state --repository /abs/control-repo \
           --control-repo /abs/control-repo \
           --workspace-repository /abs/approved-checkout \
           --workspaces-root /abs/mission-workspaces serve [--port N] \
           --auth-token-file /abs/token-file
```

The two workspace paths configure [automatic Mission
workspaces](#automatic-mission-workspaces). Without them every `dispatch`
is refused as unavailable. They are also the only repositories the delivery
tools accept: the `--workspace-repository` checkout itself, or one of its
worktrees directly under `--workspaces-root`, named by its realpath. When
that identity is checked differs by tool:

- `present_delivery` and `approve_delivery` check it **before** their
  ceremony runs, so before pr_delivery reads the repository and before any
  Git process.
- `delivery_status` first loads the delivery's record through pr_delivery,
  because only the record names the repository; it then checks that
  repository's identity, and only then projects the status. It runs no Git.

Unconfigured, all three are refused before pr_delivery is reached. Any
other path (another repository, a `..` or symlinked path, a non-repository,
a nested repository, a worktree whose `commondir` names another repository)
is refused (`grok_bot_delivery_repository_not_approved`); without
`--workspace-repository` every delivery tool is refused
(`grok_bot_delivery_not_configured`).

The check reads filesystem **shape**: realpaths, the `.git` entry, and Git's
`gitdir` and `commondir` pointer files. It is not Git's own resolution of
the repository and not proof of its identity, and it does not model every
Git behaviour. Building an accepted shape needs write access to the approved
repository's Git directory and the workspaces root, which a caller holding
only the bearer token does not have. Like the Git gates, the check is a
workflow guardrail, not an enforced boundary: it is not designed to contain
processes running with the user's own privileges. What the ceremony reads
with Git is bound into the displayed proposal, and `attest-dots` re-reads it
and refuses any change.

`serve` exposes the tools as an MCP endpoint (`/mcp`) over the Streamable
HTTP transport, tools only. It binds **127.0.0.1 only**. There is no option
that names a host, and the server refuses any other address both before
binding and before listening. It prints one JSON line with the URL, the
access control in force, and the `public_reachability` contract below, then
serves until interrupted.

Requests are also refused if:

- their `Host` is not the loopback listener;
- they carry an `Origin` header (the DNS-rebinding guard).

### Access control (implemented; required, fail-closed)

`serve` requires `--auth-token-file` and refuses to start without it, before
anything is built or bound; the server itself cannot be constructed without
a token. Every request must carry exactly one
`Authorization: Bearer <token>` header, where the token is the one in that
file. An absent, empty, malformed, wrong-scheme, duplicated or wrong token
is refused `401` before the request body is read, using a constant-time
comparison.

The token file must be:

- an absolute path to a regular file (not a link);
- readable only by its owner (`chmod 600`);
- one line of 32 to 512 visible ASCII characters.

It is read once, at startup. It is never taken from argv or the environment,
never printed, and never passed to the adapter. No token is in this
repository and none is installed.

The token is transport access control only. It shows that the caller holds
the token, never who the human is, and it **approves nothing**. Approval is
still the operator-attested relay of the human's separate reply, exactly as
before.

There is no unauthenticated endpoint: without `--auth-token-file`, nothing
is served.

### Forwarder contract

A public HTTPS forwarder must:

- terminate HTTPS on the public side;
- forward to `http://127.0.0.1:<port>/mcp`;
- present `Host: 127.0.0.1:<port>`;
- send no `Origin`;
- pass `Authorization` through unchanged.

## Final setup dependency (not performed by this code)

Grok Bot reaches custom MCP servers only at a public HTTPS URL; localhost and
private addresses are rejected. Grok Bot's cloud execution, where a Command
MCP server in a phone conversation runs, is on xAI's cloud computer.

Per current vendor documentation, **local-computer execution is a separate
capability**: when it is enabled and each command is approved, Grok Bot can
run commands on the user's own computer. That is the per-command local
shell the arming commands and the tunnel's `on` / `off` / `status` use. It
does not change how a connector reaches this endpoint, which still needs the
public HTTPS URL. See [What is known, and from where](#what-is-known-and-from-where).

These human actions are listed as data in
`grok_bot.server.PUBLIC_REACHABILITY`, and nothing in the repository performs
them.

1. `provision_bearer_token`: generate a random token (32 to 512 visible ASCII
   characters), write it to a file only the serving user can read
   (`chmod 600`), and serve with `--auth-token-file` pointing at it.
2. `provision_public_https`: provision a public HTTPS forwarder to the
   loopback listener that meets the forwarder contract above. That can be a
   tunnel per xAI's custom MCP tunneling guide, or the repository's on-demand
   Quick Tunnel, `ditunnel.py` ([On-demand tunnel](tunnel.md)).
3. `register_connector`: at grok.com/connectors, choose New Connector, then
   Custom. Enter the public URL ending in `/mcp`, and give the same token as
   the connector's credential. A Quick Tunnel's URL changes on every start, so
   with `ditunnel.py` the connector's URL is repointed every session.
4. `run_live_acceptance`: run the bounded, reversible phone acceptance
   exercise. This is what resolves the live compatibility questions below.

### Reaching the endpoint with the on-demand tunnel

[`docs/tunnel.md`](tunnel.md) is the operator documentation for
`ditunnel.py`, a free Cloudflare Quick Tunnel that runs only when asked. A
session, as instructions for a later, separately approved step (none of it
was performed here):

1. `ditunnel.py --state-dir DIR on --port PORT`, run through Grok Bot's
   per-command, user-approved local shell (default Ask every time). It prints
   the CURRENT `trycloudflare.com` URL.
2. Repoint the connector to that URL plus `/mcp`. The URL changes on every
   start, so this is per session.
3. Use the tools over the authenticated endpoint, with local arming for any
   approval.
4. `ditunnel.py --state-dir DIR off` at session end, then `status` to
   confirm. `status` never reports an old URL as active.

**Limits of a Quick Tunnel** (vendor documentation): no Server-Sent Events,
so the JSON answer is the one a tunnel carries (the endpoint's SSE branch is
kept for forwarders that can carry it), and no uptime guarantee. That the
tunnel presents the required `Host` is [U] until a live acceptance. If a
tunnel's controller is gone, stopping it is manual
([Manual recovery](tunnel.md#manual-recovery)).

### The complete path, end to end

1. **Local, already in this repository:**
   - `grokbot.py serve --auth-token-file …` runs on loopback.
   - Engineering goes `request` → `present` → local arming (`authorize`) →
     the human's `approved` reply → `approve` with the `approval_code` →
     `run` (`dispatch` … `verify`, `result`). `dispatch` needs no path: DI
     prepares the Mission's own workspace.
   - Delivery goes `present_delivery` → local arming (`authorize-delivery`)
     → the human's separate `approved` reply → `approve_delivery` with the
     `approval_code`.
2. **Human setup:** the four actions above.
3. **Human-run, outside this transport:** `python3 -m pr_delivery advance
   --delivery-id prd-…` performs an authorized delivery through pr_delivery's
   own drive (see [Completing a delivery](#completing-a-delivery-human-run-outside-this-transport)).
   Only a `COMPLETE` record is delivered. This step is not exercised by these
   tests and not reachable from Grok Bot.

## What is known, and from where

These four sources are kept apart. Each is vendor documentation, never
evidence that this code works end to end.

**Grok Bot (the phone app's Bots) and its custom connectors.**
<https://docs.x.ai/grok-bot/team-bots>,
<https://docs.x.ai/grok/connectors>,
<https://docs.x.ai/grok/connectors/custom-mcp-tunneling> and
<https://docs.x.ai/grok-bot/computer-and-apps> state that:

- Remote HTTPS MCP servers are a supported connector kind, using "the Bot's
  own credential, or each person's sign-in if the server uses OAuth";
- a custom MCP server must be reachable over the public internet;
- localhost and private addresses are rejected;
- Command servers run on "the computer each conversation uses", which is
  xAI's cloud computer (cloud execution; local-computer execution is the
  separate capability below).

**Grok Bot's local-computer execution.**

- **<https://docs.x.ai/grok-bot/computer-and-apps>** (updated 2026-10-08),
  "Your local computer is separate": cloud execution and local-computer
  execution are separate capabilities. Bots may run commands on the local
  computer when that capability is enabled and the user approves under the
  local policy.
- **<https://docs.x.ai/grok-bot/approvals-security-and-privacy>** (updated
  2026-10-06), "Control access to your local computer": Execution on Local
  Computer offers Ask every time, Always allow and Never allow. The default
  is Ask every time. Registered computers have per-computer settings, the
  prompt offers Allow once, and the controls are the same on iPhone.

That is documentation only. It is not proof that this account or device has
the capability enabled, not live interoperability, and DI does not enforce
the vendor's approval policy.

**The xAI API's remote MCP tool.**
<https://docs.x.ai/developers/tools/remote-mcp> describes a separate surface,
not the Grok Bot. It states "Only Streaming HTTP and SSE transports are
supported", and that a token is set in the `Authorization` header.

**What this repository implemented and tested.**

- The Streamable HTTP transport (POST only; JSON, or one SSE event). A Quick
  Tunnel does not carry the SSE answer, so through one only the JSON answer
  is usable.
- Exactly the MCP revisions `2025-11-25` and `2025-06-18`. Each is negotiated
  in the tests, and nothing else is advertised.
- JSON-RPC batching was removed in `2025-06-18`, so a batch is refused (`400`)
  under both advertised revisions.
- Not implemented:
  - `2025-03-26`, because it requires receiving batches and batch reception
    is deliberately not implemented;
  - the modern `2026-07-28` per-request revision;
  - the deprecated HTTP+SSE transport, sessions, rate limiting and OAuth.
- Negotiation: `initialize` echoes an advertised revision and answers any
  other with the newest one; a client that cannot speak it must disconnect.
- An `MCP-Protocol-Version` header naming any other revision gets a plain
  400, so a dual-era client can fall back to `initialize`.
- A request without that header is handled under the advertised revisions'
  rules.

The tests drive the endpoint over real loopback HTTP with a real client. That
proves **protocol shape only**, never live interoperability.

## Live compatibility dependency (unverified)

Unknown until `run_live_acceptance`:

- the MCP revision and transport the live Grok Bot client speaks;
- whether Grok Bot sends the connector credential as
  `Authorization: Bearer <token>`;
- whether its requests carry an `Origin` header;
- the `Host` the chosen forwarder presents;
- Grok Bot's tool-call timeout compared with how long a Codex Operator turn
  takes;
- whether Grok Bot shows a long `display_text` to the human whole.

### Known gap: `mcp_2025_03_26_batch_reception`

If the live Grok Bot client negotiates only MCP `2025-03-26`, it cannot use
this endpoint. That revision requires receiving JSON-RPC batches; this server
does not advertise it, and batch reception is deliberately not implemented.
The advertised revisions do not cover this case.

The minimum follow-up is to add `2025-03-26` to the advertised revisions,
together with JSON-RPC batch reception and its tests.

## What the tests prove, and what they do not

The loopback and fixture tests prove **protocol and mechanism shape only**.
In particular:

- **No live Grok Bot interoperability.** The MCP revision, headers and
  credential the live client uses are unknown (see the live compatibility
  dependency above).
- **No live tunnel behaviour.** The tunnel tests use a synthetic
  `cloudflared`; the real one was never run.
- **No proof about the local shell.** Nothing shows that this account or
  device has Grok Bot's local-computer execution enabled.
- **No proof about the installed host Git.** The `pre-merge-commit`
  lifecycle conclusion is source-based inference from current upstream Git
  (`docs/operations.md`).
- **The `mcp_2025_03_26_batch_reception` gap stays recorded.** A client that
  negotiates only `2025-03-26` cannot use this endpoint.

## Limitations

- **The request turn is read-only for writes, not for reads.** Each
  `request` runs one fresh Codex turn under the role-turn restrictive
  posture (`--sandbox read-only`, `--ignore-user-config`, `--ignore-rules`,
  `--strict-config`, `-c approval_policy=never`), verified on the exact
  argv before the process starts. It continues no session: any
  `operator_session_id` is refused (`grok_bot_operator_session_refused`)
  before any Operator turn runs. Read-only confines writes by the
  Operator's shell commands; it does not confine reads. Request text can
  induce the Operator to read, under its own permissions, any file the
  serving user can read, and the Operator's reply is returned to the
  caller. That is a material, disclosed residual: no exfiltration has been
  demonstrated, and nothing here prevents it. Read-only is not secret
  isolation, and nothing here scopes readable paths.
- **Proposal-only is an instruction, not enforced.** That the Operator only
  proposes depends on the instruction it is given; the neutralization of
  forged envelopes in the human's text is a mitigation, not a boundary.
- **Not implemented:** rate limiting, MCP sessions and OAuth.
- **Workspace trust is written to the live configuration and never
  revoked.** In production, preparation writes one `hasTrustDialogAccepted`
  key for each Mission worktree into the Claude configuration the started
  Herdr reads (`~/.claude.json`). Nothing removes a Mission worktree, so
  nothing revokes that entry either. The vendor behaviour this relies on was
  derived from `claude 2.1.251` for the Runtime's managed workspaces. That a
  linked worktree is its own Git root for the CLI's trust lookup follows
  from Git (`rev-parse --show-toplevel`); it was not observed live. The
  tests establish trust only in a temporary configuration, so they are not
  evidence of an unattended live start.
- **The Herdr-state check is not a lock.** It runs immediately before the
  intent. A herd that starts in the worktree after that check and before the
  spawn is outside what this check sees. Association proof (`reconcile`,
  `observe`) still never adopts a child that does not name this intent.
