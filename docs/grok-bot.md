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
| `present` | Returns the exact display text and the binding the human approves, and durably records what was displayed. |
| `approve` | Relays a separate reply whose whole text is `approved`. Its binding must equal the displayed one in every field. |
| `status` | Durable status and run, read from DI's records. |
| `recover` | Rebinds a request whose proposal step was interrupted. |
| `cancel` | Withdraws the caller's own pending proposal using its one-shot control capability or, if the `request` reply was lost, the request's exact `text` and `conversation_ref` (see [Cancelling after a lost `request` reply](#cancelling-after-a-lost-request-reply)). |
| `run` | Drives the request's own authorized Mission: `dispatch`, `observe`, `reconcile`, `prove`, `verify`, `result`, `pause`, `resume`, `cancel`. Each command's exact arguments are listed below. |
| `present_delivery` | The separate delivery: `pr_delivery present-dots` proposes exactly `BASE_REFRESH`, `COMMIT`, `PUSH`, `PR_CREATE` for the live candidate. It displays the complete proposal and records what was displayed. |
| `approve_delivery` | Relays the human's separate `approved` reply to `pr_delivery attest-dots`, for exactly the displayed delivery. |
| `delivery_status` | A delivery's status, read from `pr_delivery`'s record. |

Approval is **operator-attested, not cryptographically authenticated**. Grok
Bot gives DI no signed sender attribution, so DI does not establish who sent
the reply.

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
2. **The human replies in a separate message** containing only `approved`.
3. **`approve_delivery`** relays that reply with the displayed binding: the
   proposal digest and expiry.
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
  - `delivery_status` reads pr_delivery's store only.
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
           [--auth-token-file /abs/token-file]
```

The two workspace paths configure [automatic Mission
workspaces](#automatic-mission-workspaces). Without them every `dispatch`
is refused as unavailable.

`serve` exposes the tools as an MCP endpoint (`/mcp`) over the Streamable
HTTP transport, tools only. It binds **127.0.0.1 only**. There is no option
that names a host, and the server refuses any other address both before
binding and before listening. It prints one JSON line with the URL, the
access control in force, and the `public_reachability` contract below, then
serves until interrupted.

Requests are also refused if:

- their `Host` is not the loopback listener;
- they carry an `Origin` header (the DNS-rebinding guard).

### Access control (implemented; absent by default)

With `--auth-token-file`, every request must carry
`Authorization: Bearer <token>`, where the token is the one in that file. A
missing or wrong token is refused `401` before the request body is read,
using a constant-time comparison.

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

Without `--auth-token-file`, the endpoint is the loopback-only default with
no token. Never expose it beyond loopback in that state.

### Forwarder contract

A public HTTPS forwarder must:

- terminate HTTPS on the public side;
- forward to `http://127.0.0.1:<port>/mcp`;
- present `Host: 127.0.0.1:<port>`;
- send no `Origin`;
- pass `Authorization` through unchanged.

## Final setup dependency (not performed by this code)

Grok Bot reaches custom MCP servers only at a public HTTPS URL; localhost and
private addresses are rejected. A Command MCP server in a phone conversation
runs on xAI's cloud computer, not on this Mac. These human actions are listed
as data in `grok_bot.server.PUBLIC_REACHABILITY`, and nothing in the
repository performs them.

1. `provision_bearer_token`: generate a random token (32 to 512 visible ASCII
   characters), write it to a file only the serving user can read
   (`chmod 600`), and serve with `--auth-token-file` pointing at it.
2. `provision_public_https`: provision a public HTTPS forwarder to the
   loopback listener that meets the forwarder contract above, for example a
   tunnel per xAI's custom MCP tunneling guide.
3. `register_connector`: at grok.com/connectors, choose New Connector, then
   Custom. Enter the public URL ending in `/mcp`, and give the same token as
   the connector's credential.
4. `run_live_acceptance`: run the bounded, reversible phone acceptance
   exercise. This is what resolves the live compatibility questions below.

### The complete path, end to end

1. **Local, already in this repository:**
   - `grokbot.py serve --auth-token-file …` runs on loopback.
   - Engineering goes `request` → `present` → the human's `approved` reply →
     `approve` → `run` (`dispatch` … `verify`, `result`). `dispatch` needs no
     path: DI prepares the Mission's own workspace.
   - Delivery goes `present_delivery` → the human's separate `approved`
     reply → `approve_delivery`.
2. **Human setup:** the four actions above.
3. **Human-run, outside this transport:** `python3 -m pr_delivery advance
   --delivery-id prd-…` performs an authorized delivery through pr_delivery's
   own drive (see [Completing a delivery](#completing-a-delivery-human-run-outside-this-transport)).
   Only a `COMPLETE` record is delivered. This step is not exercised by these
   tests and not reachable from Grok Bot.

## What is known, and from where

These three sources are kept apart.

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
  xAI's cloud computer.

**The xAI API's remote MCP tool.**
<https://docs.x.ai/developers/tools/remote-mcp> describes a separate surface,
not the Grok Bot. It states "Only Streaming HTTP and SSE transports are
supported", and that a token is set in the `Authorization` header.

**What this repository implemented and tested.**

- The Streamable HTTP transport (POST only; JSON, or one SSE event).
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

## Limitations

- **Proposal-only is an instruction, not enforced.** The Codex request turn
  runs under the user's ambient Codex configuration with no read-only
  sandbox, so the proposal-only boundary depends on the instruction it is
  given.
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
