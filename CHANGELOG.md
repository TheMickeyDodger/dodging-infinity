# Changelog

## Unreleased

- **Herdr Missions use the same Code Diff, review, and Reception the catalog already has (Pixel Agents DI office).** An active Herdr task with a bound workspace is offered a code review of that workspace. A recorded review under `.herd/state` is shown; none recorded stays empty. Reception treats a failed Herdr task as a blocker and a completed task with no approving review as results ready for review. Delivery still names workflow Missions only, and says so. Herdr in the sidebar is the documented `herdctl` terminal path; this office does not start it, commit, push, merge, or deploy. Connections are not in this build. See [`docs/pixel-agents-office.md`](docs/pixel-agents-office.md).
- **The request workspace matches the side panel (Pixel Agents DI office).** Ask and Requests now read as part of the same product as the side
  panel: its palette, type scale, lines and controls; a compact header with quiet tabs; one persistent context area (never dismissable) instead
  of two tall banners; short You / Assistant labels with the drafting-versus-decision note said once; and counts that name what they count
  (`1 request needs you · 1 conversation waiting for your answer · 1 request in all`). An older reply with a closed technical block shows its
  prose, a view-only note and its exact original behind a disclosure, and is never listed or counted. An open request leads with its plain
  title, status and next step, with the request id, Mission, revision and digest together under "Exact references (technical)"; raw keys follow
  their plain words; the decision still states exactly what approving binds to. Smaller windows are covered: wider reading no longer squeezes
  the open request or scrolls sideways where there is little room, the header title is never clipped, and Escape returns focus to the control
  that shows the side panel when it was hidden; and where it covers a narrow window it now sits above the office's own overlays (the ambient legend no longer letters over its text) and below the dialogs. Presentation only: no change to who decides, to approve, start or withdraw, to any binding, or to the server.
  See the README and [`docs/pixel-agents-office.md`](docs/pixel-agents-office.md), "The workspace".
- **One Reception inbox, a decorative receptionist, real agents in all three grand offices, and calmer interiors (Pixel Agents DI
  office).** The side panel's attention entry and the in-world desk now open one Reception inbox: what needs you, from Mission Core, the
  delivery proposals (opt-in) and the request panel. Each card has a plain first layer with no ids or codes, a next step that agrees with
  its button, and one count shared with the request panel's own card; the exact record stays one disclosure away. A permanent, decorative
  receptionist stands at the lobby desk (not a worker, with no authority). Agents are placed across the Palace, the Botanical Atrium and
  the Deco Observatory, workstations first (a workstation faces a desk and a PC or monitor; a four-agent Mission sits 2/1/1 on workstations; a seat held now or remembered for an identity, even at a bare table, is kept), and are remembered by identity across restarts; an existing
  single-office roster is re-placed once. Office residents now stop in lobbies and corridors instead of at the desks while the offices keep
  their people, and the saved residents of the previous build carry over: restored exactly where unchanged, reset fresh where changed, and
  refused rather than guessed when a rider's plan changed. In the request panel's transcript the assistant's questions read as questions,
  with its exact reply one disclosure away. Observation only: no new authority. See the README and
  [`docs/pixel-agents-office.md`](docs/pixel-agents-office.md), "Reception: what needs you".

- **A calmer Mission panel and a full-size inspector for the Pixel Agents DI office.** The side panel now answers three
  questions first: what is running, its observed status (a word and a shape, never colour alone) and who is on it. Setup
  prose, raw codes and provenance sit behind disclosure, while stale, not-live and unverified-identity caveats stay on the
  face. "Open inspector", from a Mission or the selected agent, opens a full-size dialog with the objective as its source
  reports it (not the whole original task prompt), the team, the source fields each status came from, freshness, and task
  chains, evidence and review where the source reports them. Focus moves in and returns, Tab stays inside, Escape closes,
  and "Show in the office" takes you to the agent on the map. Mission rows exported from formatted herdctl tasks now carry
  their real objective, and where a source exported only a section header the panel says no goal was reported rather than
  guessing. Observation only: no new authority, and unknown is never shown as zero. See the README, "A calm overview and a
  full-size inspector".

- **Living city: lighting, weather and traffic for the Pixel Agents DI office.** A City button opens day/dusk/night/clock
  lighting, clear/rain/snow weather and Off/Low/Normal traffic. Traffic brings the world's own 68 lane-aligned parked vehicles to life on
  the lanes derived from its road tiles: stop lines, atomic intersections and crossings, queues, seeded turns, priority for people and real
  agents, no vehicle with any authority, held still under reduced motion, nothing saved or simulated while closed. The residents and the subway are refined:
  24 riders on journeys of three kinds (a commute, a hop, an excursion round three stations), platform tiles dealt so waiting riders never
  share a tile, posted and roaming dwell profiles, a "waiting for a train" label, and a saved snapshot bound to each resident's journey plan
  (one saved by an earlier build is ignored once). The Map draws each visible vehicle as a small box. Measured at ordinary, crowded and maximal-eligible
  loads, hidden and visible, paused, held still, across settings changes and a reload. Decoration only: it changes nothing about Missions, agents, activity,
  assignment, approval or delivery, and uses no weather service or location. Offices stay lit at night, real
  agents and street lamps carry light, weather is drawn over the outdoors only, reduced motion holds everything
  still, and the choice is saved as a four-key record (`city-presentation.json`) that is validated before it is
  written and again when it is read. The Clock mode follows this computer's local time while the view is open; it
  is a function of the clock, not a simulation. See docs/pixel-agents-office.md, "The living city: lighting and
  weather".

- **Native Herdr teams found without per-team setup, and desks that are easier to find in crowded offices.** `python3 dicatalog.py
  task-source --registry DIR --ensure` registers DI's native task source once (the directory `herdctl init` keeps its repository list in,
  `~/.config/dodging-infinity` unless `--store-dir` says otherwise); after that the teams working in an initialised repository need no
  per-team source configuration, and the Pixel Agents DI office shows their current verified roles within the limits it discloses (a team can
  still be absent: its session may not bind, its records may be stale, a source may be refused or unreadable, or the runtime listing may be
  over its fixed capacity). Without `--ensure` it only reports, as one JSON line, whether the source is
  registered and the exact command to run. It writes only the registry, never creates the registry directory, never rewrites or repairs an
  existing entry, and answers a registry it could not write with outcome `failed` and exit 5 after reading it again, so a replacement that had
  already landed is reported as unconfirmed rather than as unchanged. The runtime-agents block now says why no team shows: no task-native
  source is registered (`task_native_source_not_registered`), the registered sources were read fresh and record no roles
  (`task_native_sources_record_no_roles`, an empty roster with its counts), or some role records could not be read (counted, never called
  empty). A seat fix keeps the members of a Mission at desks ahead of idle runtime agents, whether the office loaded fresh or the team
  arrived later. In a crowded working office the office now draws a bounded number of its decorative residents at once: at most 16, and
  fewer in a small floor, ceil(T / 34) where T is the pool's resident-eligible tiles (4 of 140 in the default office, 16 of 140 in
  the expanded-office preset). The others are off duty, resting where they stand; residents take turns on duty, one hand-over per working office every six
  seconds, at a pause; the legend says "N on duty of M". Nothing is deleted or re-identified: ids, jobs, routines, journeys, the subway and saved snapshots are unchanged,
  and an older save reopens exactly. The Palace was already readable and is unchanged (its 11 residents are below the bound). The six-second
  cadence was judged from moving frames only. The resident policy is decoration only: it changes nothing about Missions, agents, activity,
  assignment, approval or delivery (the seat fix above is a separate change to which agent gets which desk). See README step 4 and docs/pixel-agents-office.md, "Ambient residents and jobs".

- **Codex Orchestrator: request, review, approve, start and follow a Mission from the
  Pixel Agents DI office.** The one deliberate exception to "the office only
  observes". A person asks for work in ordinary words (a restricted, read-only
  Codex conversation), reads Mission Core's exact proposal of record, decides on
  an exact, frozen offer by typing the exact word, presses Start, and then sees
  the Mission advance to a retrievable verified result. No delivery authority
  is added: nothing is committed, pushed, merged, released or deployed from
  here. See docs/pixel-agents-office.md, "Requesting Missions".
  - **Approval is operator-attested, not verified human authorship.** There is
    no authenticated human principal in Pixel; the panel relays the typed word,
    the record says `operator_attested_not_independently_verified`, and the
    screen says so before the decision. The ordinary unauthenticated approve
    stays refused. A withdrawal is a withdrawal, never a denial; a revision is a
    separate record; an unknown conversation turn, submission or Start is shown
    and recorded as uncertain and is never retried.
  - **Progression is automatic after an explicit Start.** `dirun.py` gains a
    MissionCore mode (`--mission-state-dir`, `--mission-control-repo`,
    `--mission-workspace-repository`, `--mission-workspaces-root`), selected
    before any workflow machinery is built; the default workflow mode is
    unchanged and the two share no lock or state. It advances only requests the
    panel's own journal submitted, approved and not withdrawn, through the new
    `MissionBridge.advance`: one step by the Mission's actual state, never a
    dispatch, and a verification recorded only when every condition holds in ONE
    fresh read. A team that stopped without succeeding is a visible hold, not a
    recorded outcome. `MissionBridge` operations take a cross-process
    per-Mission run guard shared by the panel's Start and the runtime.
  - New: `dimission_panel.py` and `mission_panel/` (journal, draft, conversation,
    lifecycle, service, loopback HTTP adapter, composition, progression pass),
    `target_runtime/mission_run_guard.py`, `mission_progress_status.py`,
    `local_request` read-only `list_requests` and `binding_status`, the
    `diMissionRequest` protocol message (eleven closed operations; the token
    stays in the office's server), and the webview workspace. Every new bound is
    exact-pinned. `scripts/di-candidate-manifest.mjs` names the frozen source
    bytes and verifies a package against them.
  - **What a person approves is what the screen shows.** The proposal's proof
    rules (what counts as done: evidence, artifacts, prerequisites, resource
    readiness, permitted blockers, the attempt and checkpoint budget) are read
    under the canonical closed shape and written out in sentences, in the
    proposal and again inside the offer that binds them. A proposal carrying
    anything the window cannot represent is not approvable from it (stated in
    words; withdrawal stays possible). A revision that changes only the rules
    is a different proposal and leaves the old display refused as stale.
  - **A long conversation is cut by bytes, not characters, and says so.** The
    panel serializes as raw UTF-8 with one shared serializer and one 4 MiB
    response bound (equal to the office relay's, checked by a test), measures a
    conversation view in those serialized bytes against a 3 MiB budget, and
    sends the most recent turns that fit with how many it holds, shows and
    leaves out, and which bound (turns or bytes) left them out. The window
    states it. An answer that would still exceed the bound is refused in words,
    never sent to be rejected mid-stream.
  - **A failed child startup leaves a diagnostic, and an in-doubt Start says why.**
    When startup fails after the roles were contracted, `start_herd` writes one
    bounded, private record (stage, failure type, each affected role's binding gap,
    session ABSENT/PRESENT/UNKNOWN) before its cleanup, reusing the agent-start
    diagnostic's redaction, private write and retention; a failing write never
    replaces the startup failure or skips the cleanup. `BindingsNotEstablished`
    carries the same gaps (its message is unchanged). The bridge's new read-only
    `startup_cause` projects the file onto the exact attempt, through the hardened
    bounded reader, bound by the run intent's own workspace and start time and never
    by recency (zero matches or several: the cause is reported as not known), and the
    panel shows it on the request view. No Mission Core or journal field was added.
    A hold stays a hold: nothing is respawned or replayed. See docs/operations.md.
  - The browser tests are real-App integration over SOURCE (Vite), not
    production-package proof.

- **Pixel Agents DI office: an early, read-only increment.** The office is
  vendored into its own subtree, `integrations/pixel-agents/`, with its own
  Node.js dependency and build boundary, alongside the read-only catalog
  bridge it reads. It observes and does not control: it does not spawn Herdr,
  approve Missions, mark Missions complete, create Git authority, deploy, or
  rewrite durable state. It is not Phase 11, which remains last. See
  docs/pixel-agents-office.md and the new README section.
  - The bridge adds `mission_sources/`, `target_runtime/catalog.py`,
    `catalog_http.py`, `mission_state.py`, `review_record.py`,
    `runtime_snapshot.py`, and the entry scripts `dicatalog.py` (source
    registration and one-shot queries) and `dicatalog_http.py` (a passive
    loopback adapter: `GET /catalog`, `/diagnostics`, `/healthz` only). Catalog
    contract version 1 stays the default; version 2 must be chosen explicitly
    at launch.
  - `mission/store.py` and `workflow_authority/store.py` expose their load
    validation as `accept_loaded_document`, so a read-only reader applies the
    same validation as `load()`. `herdr/runtime.py`'s bounded command reports
    its `process_group`. CI compiles the new modules.
  - Pixel Agents keeps its own licences (MIT code, CC0-derived character art,
    SIL OFL 1.1 font). Provenance, omitted files and the upstream update
    procedure are in `integrations/pixel-agents/UPSTREAM.md`.
  - Every dependency install in the subtree runs with `HUSKY=0`, and
    `core.hooksPath` is checked unchanged before and after.
  - **Live code review.** A live agent whose Mission Core assignment is active
    offers a read-only code review of its workspace (`GET /code_review`, the
    fifth catalog route; one dialog with a searchable file tree and a unified
    or side-by-side diff). The pinned git transport grows from eleven to a
    twenty-one-verb git transport seam: ten non-mutating live reads, each
    measured to leave the target's whole `.git` byte-identical (`git diff`
    and a default `git status` rewrite the index, so neither is used). The
    reads that can hash a working file run in a git directory of their own
    making, so a content filter the repository configures — including one
    added after the drivers were listed — is never run; a tracked submodule
    is shown by the commit it points to and is never entered. A withheld
    (private) file is not only never shown but never opened: the private
    locations are decided from the tracked names first, and every read that
    could hash or diff a file is told, in git's own literal pathspec, to leave
    them out (bounded; a FIFO where git keeps a file is never waited for; the
    untracked-read budget counts the byte that shows a file is over its bound;
    a long diff keeps its first lines, never a selection, and an end-of-file
    marker is not a line and is kept or cut together with its line; an empty
    listing says "No changes found, as far as this view can tell" and that it
    is not proof the working tree matches the commit). Where a withheld file came from (an unchanged move
    out of a private name) it stays withheld and is left out of the reads too;
    what changed in a withheld file is judged from the index's recorded metadata
    (mode, size, recorded second), not its content, and the dialog says an edit
    of the same size within that second may not be seen. A file's diff is read
    through the same private index the decision was made from, and names its
    path as a file. The dialog keeps focus and keys across asynchronous
    updates: a control that disables itself while it holds focus (Refresh,
    during a read) no longer drops focus to the page behind, where the next key
    would reach the office. A live git read ends its child process on every
    exit path, within a bound, and owns it from the moment it exists: an
    exception propagating out of the read or of the wait after it (a caller's
    interruption), a failure of the capture's own I/O, and a failure within
    the cleanup itself all kill and reap it, and the exception already on its
    way out is the one that propagates; there is no timer to fail to start or
    to cancel. The read's I/O is bounded by its deadline, not by the end of
    the stream: a pipe that ends before the process does, and a descendant
    that keeps the output open after the child is killed, can no longer hold a
    read past it (it stops, kills and reaps its child, and refuses as past its
    deadline). Every character bound of the review (a line's
    1,200, a file's 1 MiB, a path, a branch name, a section heading, a detail
    sentence, a listed filter-driver name) counts UTF-16 code units, the unit the office checks them in and
    its byte budget is built on, not Python's code points: 601 emoji were 601
    to the backend and 1,202 to the office, which refused a line the backend had
    emitted whole. The backend now measures and cuts in that unit, never inside
    a character; the office cuts its own text the same way. See
    docs/pixel-agents-office.md.
- **Grok Bot's conforming route; Grok Bot runs nothing.** A Grok Bot
  `request` now produces one DI-REMOTE-2 workflow, the same durable authority
  object the Telegram lane produces, instead of a Mission-Core proposal. A
  fresh read-only Outer Operator turn clarifies or routes, and a separate
  fresh read-only planning turn authors the Mission Authorization. It is
  validated against the control repository (`--control-repo`) and saved
  PLANNED in the workflow store (`--workflow-store`). `present` shows the
  exact rendered authorization, the local `authorize-workflow` command arms
  it, and `approve` relays the human's separate reply and moves it to
  AUTHORIZED. Only a DI Runtime running against that store advances it.
  - The record gains one required key, `origin`: the TRANSPORT origin, null
    for the Telegram lane. It is distinct from verification's target
    repository origin identity. The schema version is deliberately not
    bumped, so once this build saves a store, an older build refuses to load
    it. Every process sharing a store must be upgraded together, and there
    is no supported downgrade (docs/operations.md, "Upgrading with a shared
    workflow store").
  - Grok Bot's `run` tool starts, dispatches and advances no work. `run`
    `result` answers as the new read-only `result` tool, and every other
    call is refused by name. This supersedes the Grok Bot `run` `dispatch`
    and "run commands" statements in the entries below. The local operator
    surface (`direquest.py`) keeps its own run commands, unchanged.
  - `status` and `result` read a complete snapshot of the durable record,
    by pull. The Runtime-lock probe never blocks, and a held lock is
    reported as a process present, never as progress.
  - A request made before this route (`lr-`) can still be shown, read and
    withdrawn through Grok Bot, but no longer approved, armed, dispatched or
    advanced there. The local `authorize` command is retired.
  - Before saving its own record, each Grok `request` runs an expiry sweep.
    The sweep voids Grok-origin workflows that are still PLANNED with an
    approval that has expired and was neither consumed nor superseded, and
    it touches nothing else. Saving the new record is a separate step: at
    the store's shared 64-record cap it can prune the oldest terminal
    records of either transport, as adding any record can.
- **`dirun --runtime-config PATH`.** The Runtime's own configuration file
  (`dodging-infinity.runtime-config.v1`: exactly `schema`,
  `control_repository` and `workflow_store`) starts a Runtime for an operator
  without Telegram credentials. It advances the store that holds the file
  and writes Claude workspace trust where the started Herdr reads it.
  `--config` and the default are unchanged, and naming both flags is refused
  (`runtime_config_ambiguous`). There is no installer support for it: it
  runs in the foreground.
- **Documented limits.** Two known pre-existing defects are recorded with
  their sources: the blocking store-file open
  (`workflow_authority/store.py:288`) and the Telegram lane's
  `RecursionError` on Python 3.9 for a deeply nested reply
  (docs/grok-bot.md, "Limitations"). No live execution of the conforming
  route was exercised by this verification; its tests are hermetic
  (docs/architecture.md, section 16).

- **PR delivery to an existing open pull request (`pr_update`).**
  `pr_delivery` gains a second delivery kind: ONE new commit on the head
  branch of an EXISTING open pull request, named by exact number and head
  branch (`--pr-number`, `--head-branch`, both or neither; Grok Bot's
  `present_delivery` accepts the same two fields). It is a strict
  fast-forward of that pull request's head and authorizes exactly COMMIT and
  PUSH. BASE_REFRESH and PR_CREATE are `not_needed`, and the approved head
  is never refreshed.
  - One separate human approval of the exact displayed proposal authorizes
    both named steps. It goes through the existing ceremonies and one-shot
    receipts and the installed hooks. There is no new prompt, typed alias or
    token, and Mission approval still confers no delivery.
  - The proposal binds:
    - the `prd-` delivery id, generated when presented and minted exactly;
    - the pull request number;
    - the expected head SHA;
    - the complete candidate and its identity;
    - separately, the staged hash: the sha256 of `git diff --cached
      --binary` that the COMMIT receipt binds and the pre-commit hook
      re-checks.
  - Closed or merged pull requests, branch mismatches, head drift and a
    pull request/remote-ref disagreement each refuse before any effect, with
    their own `pr_delivery_*` code.
  - Unrelated unstaged or untracked paths may remain, read from
    NUL-separated status, when disjoint from every candidate path. They are
    never staged or committed.
  - The existing `pull_request` kind is unchanged. Its key tuples, and so
    every stored record's authority and proposal digests, are byte-identical,
    and records written before this change keep validating.
  - Known cost: a git upgrade inside the approval window can change the
    staged hash without a content change, which refuses (fail-closed).
  - Approval stays operator-attested when relayed, not cryptographically
    authenticated.

- **Automatic Mission workspaces (Task 8 final).** Dispatching an approved
  Mission no longer needs a path from anyone. Grok Bot's `run` `dispatch`
  takes no arguments, and `direquest.py dispatch` no longer requires
  `--workspace`. With `--workspace-repository` (a local checkout of the
  approved repository) and `--workspaces-root` (an existing directory outside
  every repository: no directory from it up to `/` may hold `.git` or be a
  Git directory, so a root inside a third repository, a linked worktree or a
  bare repository is refused before anything is bound or created)
  configured, the bridge does the following:
  - It prepares one isolated Git worktree per Mission at `ROOT/MISSION_ID`
    (`target_runtime/mission_workspace.py`). The worktree is detached at the
    repository's observed `HEAD` and locked with DI's marker naming the
    Mission's exact binding.
  - Mission Core records the binding as `preparing` before anything is
    created, then `prepared`, before the run intent. The intent must name
    exactly that path and baseline (`record_workspace_binding`,
    `record_workspace_prepared`, `mission_run_workspace_unprepared`).
  - A worktree is reused only on an exact match. That includes reciprocal
    worktree identity: exactly one administrative directory of the
    configured repository points back at the checkout, and the checkout's
    own `.git` pointer (a regular file) resolves to exactly that directory.
    A malformed pointer, or one that cannot be read or resolved, is refused
    durably as `foreign`, never raised as an exception.
    Collisions, foreign, conflicting, active, busy, dirty and failed
    preparations, and an unavailable repository or root, are refused
    truthfully. Each refusal is recorded durably as the latest workspace
    refusal, records no intent and starts nothing. A refusal before the
    binding creates nothing. One after the worktree was created (a Git add
    whose outcome was not confirmed, a failed post-creation check, a
    trust-establishment failure) keeps that new worktree with its
    `preparing` binding, for the next dispatch to adopt only if exact.
  - Claude workspace trust is established for exactly the new worktree
    before it is recorded `prepared`, through the existing managed-workspace
    seam (`RuntimeWorker` over `workspace_trust`: one key, one entry, in the
    configuration the started Herdr reads), and re-checked at the point of
    use. Without it a fresh directory would stop the Herdr at the trust
    dialog. A failure is a durable `mission_bridge_workspace_trust` refusal.
    `tests/test_static.py` names the bridge as the second, exact user of
    that seam; the Broker's pins are unchanged.
  - The pinned seven-verb seam gains four local worktree verbs
    (`toplevel`, `common_dir`, `worktree_list`, `add_worktree`). It is now
    an eleven-verb git transport seam, still the only subprocess seam, with
    no remove, prune, move, force or delivery verb.
  - Before the intent, a worktree holding any Herdr task or runtime record
    (Git ignores `.herd`) is refused.
  - Restarts and overlapping preparations converge on one worktree; the
    once-only intent and the HOLD after an uncertain dispatch are unchanged.
    A crash inside `git worktree add` can leave a partial directory. It is
    refused, because it is never exact, and kept: never adopted, completed
    or deleted.
  - Nothing removes, prunes or unlocks a worktree.

  The explicit `workspace_path` (`--workspace`) stays as an operator recovery
  path only. It must name the Mission's own, already bound worktree, it never
  creates a workspace or a binding, and every check applies to it. A lost
  binding is never rebuilt from a path or a marker. Before this change, any
  clean checkout of the approved repository was accepted. `status` shows the
  binding and the latest refusal. The docs now state the active position:
  Grok Bot is the human-facing mobile interface, Codex is the Outer
  Operator, and Dots is optional for active operation. The earlier
  statements are kept, labelled as history.
- **Grok Bot transport over the current contracts (Task 8, local candidate).**
  `grokbot.py` / `grok_bot/` is a thin transport, not the retired `grok_mcp`
  spike. Plain text goes to the Codex Outer Operator (`operator_session`,
  source `grok_bot`), which authors the Mission proposal. Presentation,
  operator-attested approval, status and the run commands are the local
  request surface's own. `present` shows the complete proposal, with nothing
  truncated, and durably records what was displayed. `approve` compares the
  relayed binding with that record and refuses any difference by name. A
  loopback-only (`127.0.0.1`) MCP Streamable HTTP endpoint (`grokbot.py
  serve`, optional bearer token) advertises exactly MCP `2025-11-25` and
  `2025-06-18`. Delivery stays a separate ceremony: `present_delivery` and
  `approve_delivery` relay pr_delivery's own `present-dots` and `attest-dots`,
  unchanged, for exactly the displayed delivery proposal. They record an
  operator-attested PR Delivery Authorization for BASE_REFRESH, COMMIT, PUSH
  and PR_CREATE only. An engineering approval never reaches that ceremony,
  the transport mints nothing and performs no step, and no tool merges,
  releases or deploys. `grok_bot/delivery.py` is the second module outside
  `pr_delivery/` allowed to import it (`tests/test_static.py`). Its tests run
  the ceremony over a recording double for the whole transport, with every
  process seam contained. Limitations kept: approval is operator-attested,
  not cryptographically authenticated; loopback tests demonstrate protocol
  shape only, not live Grok Bot interoperability; and a client that speaks
  only MCP `2025-03-26` cannot use the endpoint, because batch reception is
  not implemented. Public reachability (HTTPS, connector registration, live
  acceptance) is a human setup step (`docs/grok-bot.md`).
- **Local operator request surface (Task 8, increment 2, local candidate).**
  `direquest.py` / `local_request/`: any local caller proposes a bounded
  Mission (Mission Core proposal, revision, digest), reads durable status
  (Mission Core's observation, unchanged), and may withdraw only its own
  pending revision-1 proposal with the one-time key it received. Approval is
  always refused (`local_request_approval_unauthenticated`); nothing is
  dispatched. Mission Core gains the proposal-only principal kind
  `unauthenticated_local_caller` (refused in the core for every decision,
  reservation, state operation and authorization) and a durable withdrawal
  marker its decision path refuses on (`mission_proposal_withdrawn`). The
  marker is not a lifecycle state: the Mission stays `AWAITING_DECISION`.
  No running-Mission control; authenticated Dots approval stays NOT
  ESTABLISHED.
- **Grok Bot interaction surface retired (Task 8).** The Grok Bot MCP spike,
  the `grok_mcp` package and the `grokmcp.py` entry script, is removed from the
  active product and its startup path: no installed console entry, no
  compile-list entry, no product importer. `tests/test_grok_bot_retirement.py`
  pins its absence and starts every installed entry with any `grok_mcp` import
  refused. Neutral coverage it carried moved to Mission Core directly (recorded
  approval provenance), and two trust pins became exact: no product transport
  calls `apply_human_decision`, and `pr_delivery/mission_parent.py` is the only
  product importer of `mission` outside the package. Grok as a model or Herdr
  agent runtime is unchanged. The docs now name Dots as the human-facing mobile
  interface, with what is demonstrated (reach, read, and reply; a connected
  same-task durable status read) separated from what is not established
  (authenticated exact approval binding, outage and restart recovery,
  background notification, verified-result delivery fidelity, full live
  Mission acceptance). Mission approval over Dots fails closed.
- **P1-A6 Verified PR Delivery.** A separate durable human PR Delivery
  Authorization (`pr_delivery/`, minted only by a terminal ceremony) binds the
  exact reviewed candidate identity, the recorded Herdr COMPLETE, canonical
  Reviewer APPROVE and independent verification evidence, and the closed action
  set BASE_REFRESH, COMMIT, PUSH, PR_CREATE. One-shot receipts derived from it
  are accepted by the installed git guards as a second path beside the manual
  `approve-commit`/`approve-push` tokens; disjoint fast-forward base drift is
  refreshed automatically with the candidate re-proven; every effect
  reconciles after a crash without a duplicate; delivery stops at an open pull
  request with no merge, tag, release, deploy or publish verb. `herdctl
  delivery-evidence` emits the Herdr evidence the ceremony binds.
- **PR Delivery binds staged candidates of any size.** COMMIT's
  `staged_sha256` (`DeliveryTransport.staged_diff_sha256`) now streams
  `git diff --cached --binary` through an incremental sha256 and a
  finalized strict UTF-8 decoder instead of capturing it. A staged diff
  over the 1 MiB `MAX_TRANSPORT_OUTPUT_BYTES` bound previously made COMMIT
  retry before any receipt existed. The digest and the invalid-UTF-8 and
  git-failure refusals are unchanged, and the bound and its refusal still
  apply to every other transport capture.
- **A completed run's receipts are recorded after its authorization
  expires.** `validate_delivery_parent_use` keeps the run that consumed a
  Mission Authorization eligible as the `github_pr` delivery parent after
  that authorization expires. The Mission store nevertheless refused the
  run's validated receipt attestation (`mission_state_authority_window`),
  so nothing was recorded. The store now checks that expiry against the
  run's intent instead of the attestation. That applies only when the
  Mission's one run intent consumed exactly that authorization at the cited
  revision before it expired, and the run reached RUNNING. The `issued_at`
  floor, the revocation bound, every attestation binding and the full
  window for activations and completions are unchanged.

## v0.7.0

- **AUTOMATED — unattended remote runtime hardening.** Added managed workspace
  trust automation; durable role/session/process/workspace ownership; target
  bootstrap and readiness receipts; deterministic dispatch reconciliation;
  Runtime health, vintage, and turn evidence; PATH-aware role-turn spawning;
  and permanent post-commit regression instrumentation. The PATH repair uses
  PATH-aware `execvp` semantics so production can launch the installed `codex`
  executable without weakening process/session ownership.
- **AUTOMATED validation.** The corrected increment passed 1778 tests with 0
  skips, all static checks, and `git diff --check`.
- **LIVE mountain — TERMINAL, ended BLOCKED.** A natural-language Telegram
  request targeting an external repository issue triggered a separate fresh
  restrictive planning process and a valid
  Mission Authorization that preserved the human request, bound the control
  repository and target baseline
  `main @ 3e1833d930723ef4f7220698c98155a925591d4d`, retained
  `delivery_authority = none`, and required a one-shot Telegram button
  approval. Runtime independently claimed the durable authorization without a
  Gateway engineering turn, materialized and trusted the isolated target,
  prepared and validated the handoff, durably bound the target engine, and
  bootstrapped Supervisor/Lead/Executor/Reviewer. Supervisor owned strategy;
  Lead, Executor, and Reviewer ran normally; target Herdr task
  `20260830-094026-9fef2d` reached COMPLETE; a canonical target Reviewer
  APPROVE was recorded; and target observation refreshed from a stale `ACTIVE`
  reading to `COMPLETE`. Workflow `wf-2c901885473fc4781bf82296` then exposed
  genuine post-dispatch policy drift and correctly terminated BLOCKED at
  `broker_verification_policy_drift`: `verified_result` and `result_delivery`
  remained null, no target Git delivery occurred, and the target stayed at
  baseline carrying an implementation diff only.
- **Final delivery certification.** The historical live mountain reached target
  completion and exposed the delivery-contract defects that blocked release.
  The corrected final-result path is now certified hermetically: a dedicated
  bot-owned result placeholder is durably bound before dispatch; verified
  results edit that same Telegram message; ambiguous placeholder creation
  fails closed; crash-after-edit recovery reconciles against the same object;
  and placeholder-bound workflows can never fall back to a second
  `sendMessage`. A fresh post-fix live mountain is not used as release
  evidence. This release certifies verified final-result delivery; separate
  artifact-delivery behavior is not claimed by this certification. Git,
  release, and deployment authority remain separately human-gated.
- **Runtime stabilization — integrated.** Historical stabilization commit
  `d8ec2af409e4086f985be03371a872a84a3767ec` on branch
  `fix/runtime-terminal-reconciliation` (Herdr task `20260830-185309-4c3db7`,
  COMPLETE, final canonical Reviewer round 6 APPROVE) was reviewed and pushed
  before being integrated into `main` for v0.7.0. Its historical validation
  remains preserved: focused regression 159/159;
  `tests/test_target_runtime.py` 250/250; static checks PASS; Python 3.9.6
  compile PASS; `git diff --check` PASS. The historical repository-wide LIVE
  working-tree loop stood at 35/37 solely because pre-existing live `.herd`
  specimen assertions in `tests/test_hermetic_git.py` and
  `tests/test_reconcile_audit.py` predate that task. The historical red
  evidence remains preserved; the release no longer depends on an unmerged
  stabilization branch.
- **Clean-clone CI evidence.** Commits
  `52a97b71a3b5c9f20ff33d4feb1332284cd825b7` and
  `4eea64f2a915e988dbfd73ad51dd9f6546bc6a8f` made the CI hermetic; the final
  GitHub CI run `33330263889` is green across all four macOS/Ubuntu x Python
  3.9/3.13 jobs.
- Added the Remote Mission Fabric roadmap. Exact, one-shot, mission/result-bound
  Telegram commit authorization followed by separately authorized exact push
  and PR actions is now a Phase-I product requirement, not implemented behavior.

- **DI-REMOTE-2 release certification.** Fresh continuation task
  `20260901-165812-045b0c` completed normally after adversarial Reviewer
  correction rounds. The accepting review was persisted `valid=true`,
  `APPROVE`; the authoritative unchanged-tree discovery ran 2,048 tests in
  403.984 seconds with `OK (skipped=1)` and exit 0. R-17, R-18, and final I5
  exactly-once delivery are directly mutation-pinned.
- **Herdr certification topology.** The continuation herd that certified this
  release ran Supervisor on `claude-fable-5-1`, Lead on `opus`, Executor on
  `claude-fable-5-1`, and the independent Reviewer on `gpt-5.6-sol`.

- Herdr observability is schema version 3. The read-only projection gained
  `vintage` (which task a state file belongs to, and the artifacts that
  disagree about it), `checkpoint`, `roles` (role-to-agent bindings observed
  rather than assumed) and `turns` (turn records for the current task,
  including records a turn's own death would otherwise have swallowed). A role
  with no turn recorded is OMITTED from the listing rather than rendered as
  healthy, and a turn record written by a different build of the observer is
  reported as BUILD SKEW naming both builds.
- The model a RUNNING agent uses is not observable through the agent
  interface, and model reporting now says so rather than implying otherwise.
  The projection's role model key is `configured_model` and the human render says `model-CONFIGURED=`; the
  unqualified `model` key is gone rather than joined by a qualified sibling.
  The projection carries no `running_model` field, no `model_observable` flag
  and no verdict about a running model, because such a field would imply a
  distinction the evidence cannot support — and the projection states that
  limit in its own diagnostics, so a JSON-only consumer meets it without
  reading the documentation. A role whose configuration names no model renders
  `(unset)` rather than a guess.
- The managed workspace trust boundary is documented in three parts in
  SECURITY.md — what establishment does, the exact scope it is confined to,
  and how it fails closed outside that scope — including the one path where it
  does not fail closed (the CLI's unlocked fallback on lock contention) and the
  point-of-use re-verification that bounds it.

- Completed DI-REMOTE-2 correctness (verification, dispatch recovery, and final-result delivery; released in v0.7.0 and certified hermetically). VERIFIED is now a Broker-decided conjunction: the fresh verification turn's `verified_result` is NECESSARY, NEVER SUFFICIENT — eight conjuncts (ten independent problem codes) are applied against a fresh disk read, the canonical target Reviewer APPROVE among them as TARGET-PRODUCED evidence that the target's own review process ran and concluded (never independent verification), and Herdr lifecycle COMPLETE alone can never verify. Observation completeness is SOURCE-SCOPED (ruling R-6): a decision is blocked only by a demoting diagnostic in its registered consumed-source set, the RAW global completeness is recorded and rendered unaltered, and an agents-unprobed global PARTIAL is EXPECTED in production. Verification consumes a bounded, streamed, read-only evidence projection (the git transport seam gained read-only `diff_head` and `status_porcelain_readonly` verbs and is now seven verbs, plus a `.pyc`-immune protected-surface digest) rendered into the verification prompt. Dispatch stamps a protected-surface receipt (`protected-surface baseline at dispatch`); a workflow dispatched before the receipt existed FAILS CLOSED at verification. An identity-unresolved dispatched workflow recovers deterministically on durable state — one fresh `status_recovery` turn, then the evidence-only `reconcile_dispatch` action that binds EXACTLY ONE provable existing child (exact leased-workspace realpath plus the lease's own observed task id) or stops durably BLOCKED; under ruling R-3 it reads NOTHING outside this repository, the derived alias is a derived expectation only (never binding evidence), and more BLOCKED outcomes are the accepted cost. Record-growth containment: a record at a hard bound stops that ONE workflow durably with a truthful capacity code (`broker_record_capacity_exhausted` / `runtime_codex_turn_capacity_exhausted`) and never kills the Runtime process. ATTRIBUTION: the permanent-PARTIAL stall and the never-executable production role-turn wrapper were BOTH inherited from accepted task 20260826-022933 and are corrected here; the record-growth containment class was found and closed across two increments of this work (instance-wise, then structurally). A GENERAL stop/refusal-reason mechanism is a DEFERRED follow-up candidate (human scope decision): verification and recovery blocks carry scoped `/status` reasons, and refusals outside those two scopes leave no record receipt today.
- Added DI-REMOTE-2 Remote Target Repository Routing (released in v0.7.0; the complete lifecycle is proven hermetically, and the production path was exercised once on a historical live mountain that reached target Herdr COMPLETE and then terminated BLOCKED on post-dispatch policy drift): an allowlisted Telegram user can authorize one exact bounded cross-repository mission while Dodging Infinity remains the permanent control and policy repository. Routing is route (b): the LEGACY Codex turn's response is a ROUTING SIGNAL ONLY — its DI-REMOTE-2 marker carries NO authority and its body is discarded unread — and it triggers a SEPARATE, fresh, restrictive planning turn (its own read-only Codex process, no resume, no session id) that PRODUCES the Mission Authorization; a legacy turn that instead returns a DI-REMOTE-1 plan envelope keeps the v1 local path byte-unchanged. The Mission Authorization is a closed-schema document (destination and boundaries only — implementation-strategy keys are refused by normalized name at any nesting depth) rendered exactly to the phone and approved one-shot on the bound message; approval consumes durably into the new `workflow_authority/` store (atomic, cross-process-locked, fail-closed, never silently reinitialized, group/other-accessible files refused on load) and dispatches NO gateway turn.
- Added the DI-REMOTE-2 Runtime (`target_runtime/` package, `dirun` entry script, optional per-user LaunchAgent via `scripts/dirun-agent.sh`): a separate local process coupled to the control chain only through the durable workflow store. It claims authorized workflows and advances the FULL lifecycle automatically — PLANNED → AUTHORIZED → WORKSPACE_READY → PREPARED → VALIDATED → DISPATCHED → VERIFIED → COMPLETED (plus BLOCKED and NEEDS_REAUTHORIZATION) — each forward transition authorized by a Runtime-issued one-shot capability that the narrow Target Broker independently validates and consumes. It materializes an isolated managed workspace (containment, canonical remote identity, and approved baseline verified from the clone; traversal structurally unrepresentable), discovers bounded target instruction files, runs a fresh read-only Codex handoff-validation turn that is SHOWN the ACTUAL bounded instruction content under a closed status set (`request_dispatch` / `needs_reauthorization` / `blocked`), and dispatches the BYTE-EXACT stored handoff bytes through the existing structured spawn bridge with the supervisor-first surface `{target_repo, task, alias}` — so after Approve Mission there is no manual step. After dispatch it OBSERVES the target through the existing read-only Herdr observability (herd's own stopped set drives the stopped decision under SOURCE-SCOPED completeness — see the correctness entry above; a projection degraded in a CONSUMED source WAITS and the last distinct observation is surfaced in `/status`), runs a fresh verification turn whose `verified_result` is necessary but never sufficient, and returns the verified result to Telegram through the exact bound result placeholder: the bot-owned message is durably bound before dispatch and the verified final result edits that same message; placeholder-bound workflows never fall back to a second `sendMessage`. Bounded corrective follow-ups are an AUTHORIZATION-SCOPE bound (2), NOT a review-round limit: exceeding it transitions durably to NEEDS_REAUTHORIZATION. Failed or ambiguous states fail closed durably and surface through `/status` with concrete remedies; `delivery_authority` is structurally `none` (no subprocess outside the pinned seven-verb git transport seam; no delivery verb appears in the package, enforced by an exact literal scan and by subprocess confinement; the acceptance narrative asserts no delivery or mutation verb reaches the target, and every transport argv is pinned as an exact literal in the Runtime suite).
- BREAKING (workflow store schema): the durable `workflow_authority/` store is schema-2. A schema-1 workflow record is RETIRED, never upgraded — `tgop migrate-workflows` moves v1 records into a preserved byte-exact backup and never fabricates the v2 authority fields (exact human intent, unresolved questions, execution scope) a v1 record lacks. This is separate from the schema-1 adapter `state.json` break below.
- BREAKING (explicit migration, documented since it shipped in the I1 increment but previously undocumented here): an existing schema-1 Telegram adapter `state.json` now fails closed at startup until the human runs `tgop migrate-state`. The migration keeps a byte-exact v1 backup and marks every pre-existing approval superseded FOR V2 PURPOSES ONLY — v1 local semantics are unchanged, and an old approval can never authorize a v2 target (a second, structural layer refuses v1 approvals on every v2 path independently of the marking).
- AUTOMATED/LIVE boundary: the DI-REMOTE-2 suite remains hermetic (fixture git,
  injected Telegram/gateway/role-runner/spawn, injected-or-real read-only
  `herdr.observe` over local fixtures). Production separately exercised live
  Telegram v2 traffic, GitHub target materialization, target Herdr bootstrap and
  dispatch, and fresh installed-Codex role turns through a target task COMPLETE
  with a canonical target Reviewer APPROVE — on a mountain that then terminated
  BLOCKED. The corrected final exactly-once contract is certified by the
  release suite and adversarial review; it was not rerun as a fresh post-fix
  live mountain, so release claims remain bounded accordingly. DI-REMOTE-2 is
  released in v0.7.0.
- `scripts/install.sh` now also installs a `dirun` wrapper. The recorded A0 codex-cli 0.149.0 sandbox-telemetry limitation is documented verbatim in SECURITY.md.

## v0.6.3

- Added the Telegram Remote Operator MVP (`telegram_operator/` package plus the `tgop` entry script): a trusted local Mac adapter that receives allowlisted private-chat Telegram intent and routes it exclusively through the Codex Gateway into the Codex Operator. It authenticates every update on its identity envelope before parsing any content (exact numeric user-id allowlist, private chats only), returns bounded plans through a versioned remote protocol (`plan`/`status`/`result`/`error` envelopes; free-form model text is never reinterpreted as a plan or result), offers one-shot Approve/Reject inline buttons bound to user + chat + repository realpath + gateway request + Codex session + plan message + exact plan digest + adapter-held nonce + `expires_at` — with the controls attached to the plan message only after complete delivery of the plan text is proven and the exact message binding is durably persisted (undeliverable plans are refused before any approval exists; truncated, partial, failed, or unverifiable deliveries and failed button offers void the approval) — resumes the bound Codex session on approval, reports durable-state-first `/status` with a separately constrained read-only Operator turn (acknowledged immediately, answered serialized behind active Gateway work; no unsolicited progress delivery), and reports interrupted work honestly after restart without ever replaying authority. Remote delivery authority (commit/push/PR/tag/release/deploy) is deferred and has no remote path.
- Telegram transport uses genuine outbound Bot API long polling (positive server-side long-poll duration; client socket deadline strictly greater by a hard constant margin; deadline on an idle poll handled as a normal empty poll) with capped retry/backoff, 4096-character message chunking with labelled truncation, bot-token redaction on every diagnostic surface, atomic JSON state outside the repository, a single-instance lock, and an optional per-user LaunchAgent (`tgop install-agent` / `tgop uninstall-agent`). The adapter never imports or invokes Herdr/herdctl and holds no orchestration-state path strings — enforced by the extended three-part isolation check in the static suite plus a behavioral audited filesystem test. `scripts/install.sh` now also installs a `tgop` wrapper; CI compiles the gateway and Telegram sources.

## v0.6.2

- Added Codex Gateway v0.1: a local, transport-neutral terminal boundary (`codex_gateway/` package plus the `codexgw` entry script) that routes human intent only into the existing Codex Operator workflow via the locally installed `codex` CLI. It validates the target repository (git worktree with `AGENTS.md` and `OPERATOR_PROTOCOL.md`) before any invocation, carries intent on stdin, supports stateless session continuation via `--resume SESSION_ID`, and maps outcomes to deterministic exit codes (0 completed, 2 invalid request, 3 codex unavailable, 4 codex failed, 5 malformed output) with a versioned `--json` result contract that always includes an `unrecognized_event_lines` count of output lines matching no declared event shape (disclosed on stderr in text mode when above zero). Both byte boundaries are guarded: intent (stdin/argv) must be valid UTF-8 and is refused as `invalid_request` otherwise, and Codex output is captured as bytes and decoded strictly, mapping an undecodable stream to `malformed_output` with a distinct `codex_output_not_utf8` error code. On the byte boundaries the gateway itself controls — intent in, Codex streams out — it never prints a traceback and always exits within {0,2,3,4,5}; like any Python CLI in this repository, rendering a valid non-ASCII message can still fail if the interpreter's stdout encoding is overridden to one that cannot represent it (e.g. `PYTHONIOENCODING=ascii`).
- The gateway recognizes Codex JSONL events only through a declared compatibility surface (named constants in `codex_gateway/codex_adapter.py`) and fails closed with `malformed_output` when no recognized terminal message is found; session handles are reported honestly as null when absent. Sandbox-weakening and check-bypassing Codex flags are banned by an in-code argv guard. The gateway never imports or invokes Herdr/herdctl — enforced by a three-part architectural isolation check (AST walk, token scan, behavioral import probe) in the static suite. `scripts/install.sh` now also installs a `codexgw` wrapper.

## v0.6.1

- Added `herdctl observe [--repo NAME] [--json]`: a schema-versioned (v1), strictly read-only, point-in-time projection of one repository's herd — repository/Git identity, config/workforce, mission, current task, runtime topology, bounded allowlisted live-agent state, recorded child dependencies, review metadata, artifact presence/freshness, bounded recent task summaries, and explicit per-source diagnostics. Human mode prints a concise summary; `--json` prints the canonical projection and nothing else. Exit is 0 whenever an observation is produced (even a fully PARTIAL one); only an unresolvable `--repo` reference exits 2. All logic is package-owned in `herdr/observe.py`.
- `herdctl observe` is hard-bounded by module constants never derived from repository input: 1 MiB state-file read limit, 64 live agent probes per run (config-expected roles first), 32 listed agents, 10 recent tasks, 40 review files, 32 children, 16 artifacts, 200-character truncation of every projected string, and a 2000-entry cap on directory scans. Missing, malformed, unreadable, or oversized sources degrade to explicit diagnostics — never a traceback, never a repair, never a mutation; Git queries run under `--no-optional-locks` so even `.git/index` stays byte-identical. The legacy `events.jsonl` journal is reported only under `legacy` and never presented as current activity.

- Added `herdctl health [--repo NAME]`: a repository-scoped, strictly read-only operational readiness probe. It checks herd configuration, Herdr server reachability, runtime state, that every expected agent resolves to a live Herdr agent, and task-state readability. Agent workflow states (idle/working/done/blocked) are reported as information, not failures; missing, unreachable, malformed, or unknown infrastructure fails with an actionable remedy and a nonzero exit. Complements `doctor` (environment/tooling probe) and `status` (task + agent state display).
- `herdctl health` bounds live-agent probing to a hard cap of 512 runtime entries per run (expected roles probed first), so a corrupt runtime map can never fan out unbounded `herdr agent get` subprocesses. A map exceeding the cap fails health with a count of the unprobed entries instead of reporting READY on unverified agents.
- `herdctl health` remedies no longer advertise `herdctl bootstrap --force` for the runtime states bootstrap cannot read past — invalid JSON, a non-object payload, a non-object `agents` value, or a corrupt recorded supervisor name — since bootstrap re-reads `.herd/state/runtime.json` before its force check and tracebacks on those states with or without `--force`. Those remedies now say to move the file aside or restore a valid JSON object first, then run plain `herdctl bootstrap`; `--force` is still advertised where it genuinely works, such as a corrupt non-supervisor agent name.
- Restored `AGENTS.md` and `OPERATOR_PROTOCOL.md` as the repository-level Codex Operator contract and operating protocol, preserving Herdr as the engineering execution layer and delivery as a separate human-authorized boundary.

## v0.6.0

- Replaced the abandoned Mission Control architecture with a simpler Codex-as-operator model: human intent flows through Codex into Herdr's existing Supervisor, Lead, Executor, and Reviewer hierarchy.
- Added a durable mission contract under `.herd/state/mission.json`.
- Added `herdctl mission create` and `herdctl mission show`.
- Added `herdctl task --mission` to dispatch Codex-prepared missions through the existing Herdr Control Plane.
- Mission dispatch now carries the full objective, constraints, rules, acceptance criteria, and verification plan into the Supervisor task payload.
- Mission rules continue to flow through Herdr task policy for enforcement.
- Strengthened bootstrap boundaries so agents do not infer engineering work from repository state, verification commands, or shared memory before receiving an explicit task or delegation.
- Fixed one-shot push approval lifecycle so `git push --dry-run` does not consume authorization; approval is consumed only after the approved commit is observed on the approved remote-tracking ref.
- Added exact annotated-tag push authorization through `herdctl approve-push --tag` and `herdctl push-tag`.
- Added regression coverage for mission dispatch, push dry-runs, real pushes, fetch behavior, approval invalidation, and the updated operator workflow.
- Removed the abandoned Mission Control implementation while preserving Herdr's canonical CLI, orchestration engine, repository isolation, and human commit/push gates.

## v0.4.0

- Added the first Mission Control layer with the `infinity` full-screen terminal interface, multi-Herd navigation, live orchestration visibility, an append-only activity journal, snapshot API, optional browser interface, non-destructive Herd close/unregister behavior, Ghostty session foundations, and machine-readable handoff signaling.
- Kept Herdr as the canonical orchestration engine and retained protected Git commit/push gates. The Mission Control architecture was subsequently removed in v0.6.0.

## v0.3.0

- Renamed the project to **Dodging Infinity**: an orchestration system for bounding unbounded objectives into finite, isolated, verifiable work.
- Added package-owned `HerdrControlPlane` as the primary programmatic orchestration interface.
- Added autonomous Supervisor-to-child-Herdr delegation through a structured orchestration bridge.
- Added durable parent/child task dependency tracking and deterministic parent completion gating.
- Added repository-scoped `herdctl rules`, `rules add`, and `rules remove` commands.
- Added repeatable task-scoped `herdctl task --rule` constraints that do not persist into repository configuration.
- Added hierarchical repository isolation: cross-repository implementation is delegated to separately scoped child Herdrs rather than performed directly by the parent.
- Extracted package-owned initialization, lifecycle, heartbeat, task dispatch, Git guards, registry, policy, runtime, orchestration, and dependency services under `herdr/`.
- Replaced reliance on Herdr's short initial prompt-settlement gate with harness-owned prompt observation and settlement.
- Added one safe retry for an unobserved bootstrap delivery while preserving single-delivery semantics for normal task prompts.
- Formalized model/runtime agnosticity: Supervisor, Lead, Executor, and Reviewer are logical roles that may be backed by any Herdr-supported model or runtime.
- Expanded regression coverage across policy, Control Plane operations, runtime prompting, lifecycle, initialization, heartbeat, guards, orchestration, dependencies, completion gating, and rules UX.

## v0.2.3

- Added built-in `max-quality`, `all-claude`, and `conservative` workforce presets.
- Added `herdctl init --preset ... --test-command ...`, `herdctl presets`, `herdctl preset`, and `herdctl set-test`.
- Added strict deterministic Reviewer protocol validation through `herdctl review-decision`.
- Reviewer transcripts are now persisted by the harness under `.herd/state/reviews/`, allowing Reviewer runtimes to remain read-only.
- Added one-shot human `approve-push` tokens bound to repo/branch/HEAD/remote/target.
- Added Git `pre-push` guard while preserving any pre-existing hook input.
- Expanded Claude PreToolUse protection to pushes, including `--no-verify` and destructive/force push forms.
- `safety-install` now also configures conservative Claude Auto Mode `$defaults` at user scope while preserving existing entries.
- Context reset is runtime-aware: Claude `/clear`, Codex `/new`; unknown runtime reset commands must be configured explicitly.
- Doctor now checks Codex when configured and validates the repository push guard.
- Strengthened repo-boundary and Git-boundary role contracts.

## v0.2.2

- Added deterministic ACTIVE/COMPLETE/ABORTED/ERROR task state.
- Added idle-aware Supervisor heartbeat.
- Added context checkpointing and bounded task history.
- Added `clear-contexts`, task completion/abort/status, and rejection-loop drill.
- Added safer reads for working/blocked alternate-screen agents.

## v0.2.1

- Fixed Herdr fresh-pane startup race by waiting/retrying for interactive shells.
- Added cleanup of partial failed workspaces.

## v0.2

- Added multi-repo registry and repo isolation.
- Added human one-shot commit authorization bound to exact repo/worktree/branch/HEAD/staged diff.
- Added Git reference-transaction history guard and Claude PreToolUse commit guard.
