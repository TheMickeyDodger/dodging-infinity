Pixel Agents DI office — standalone package

WHAT THIS IS
  The Pixel Agents early read-only increment: a pixel-art office that SHOWS what a Dodging
  Infinity (DI) backend reports about Missions, their roles, reports and reviews.
  PASSIVE means: NO Mission, agent or control mutations. It never starts, stops, registers,
  unregisters or steers anything, and never sends terminal input or invokes a model.
  It is NOT "writes nothing": it writes its OWN local layout, settings and seats, and its
  per-launch access file, inside its own state dir (your isolated profile) — e.g. when you move
  a desk in the layout editor.
  Consequential action lives in the existing DI, Grok Bot and Codex action routes. They are
  NAMED HERE AS DOCUMENTATION ONLY: this office never invokes them and has no route to any of
  them.

RUN (no install: extract the package and run it)
  Node >= 20. The runtime dependencies are in node_modules/.

  node dist/di-cli.js --state-dir <absolute dir> --backend-discovery <absolute file> \
       [--contract-version 1|2] [--port <n>]

  --state-dir           the office's own state root (settings, layout, seats); never ~/.pixel-agents
  --backend-discovery   the DI backend's discovery file, READ ONLY — the office never starts,
                        supervises or restarts the backend
  --contract-version    the ONE catalog contract version this process reads (default 1); a
                        response of any other version is shown as INCOMPATIBLE

  The office binds 127.0.0.1 only. It prints the path of its access file, not the URL. Open the
  URL stored in <state-dir>/run/browser-access.json.

BUILD FROM SOURCE (what was actually done for this package, from the office source root:
integrations/pixel-agents/ in a Dodging Infinity checkout)
  Install — LOCKED, OFFLINE, and with git hooks left alone:
    HUSKY=0 npm ci --offline --cache <a populated npm cache dir> --foreground-scripts --no-audit \
         --no-fund --no-update-notifier
    (with SCARF_ANALYTICS=false DO_NOT_TRACK=1). The cache already held every locked package,
    so nothing was fetched; npm ci resolved strictly from package-lock.json.
  Never a plain `npm install`: the package's "prepare" script runs husky, which can rewrite git
  hooks — and integrations/pixel-agents/ has no .git of its own, so the hooks at stake are those
  of the Dodging Infinity repository. HUSKY=0 disables it. ASSERT, before and after the install,
  that core.hooksPath is UNCHANGED (git config --local / --global / --system core.hooksPath) —
  that assertion is the point; for this package it was unset before and unset after.
  Then each build step ran with an ISOLATED HOME, the same npm cache, HUSKY=0 and npm offline:
    npm run build                     # asyncapi:generate, check-types and lint, then
                                      # node esbuild.js (dist/di-cli.js and the other entries)
                                      # and build:webview (the production webview, dist/webview/)
    node scripts/build-di-package.mjs --out <absolute dir>
                                      # assembles this package, its .tgz and a .tgz.sha256 sidecar

THE BACKEND (launched by its owners, never by this office)
  THE REGISTRY THIS OFFICE CONSUMES is a SEPARATE, DEDICATED one, bound to exactly two sources:
  pixel_live_office (mission_core) and di_v2_store (workflow_store) — registered as in section 2
  below, and separate from the v1 runtime's registry.
  Sections 0-5 below come from the backend's own V2-LAUNCH-AND-LIMITS.md (export
  exec1-export-9b3da846-r3b, sha256 14c567bd350bb33c5367161f7f78608bdfe198bd1c9d29127269f62484145bd0).
  Sections 3, 4 and 5 are copied VERBATIM. Sections 0, 1 and 2 are ADAPTED: the backend is
  launched DIRECTLY FROM A DODGING INFINITY SOURCE TREE, whose repository root holds
  dicatalog.py and dicatalog_http.py, instead of from a separately delivered backend package
  (that package is not built by any command in this repository), and machine-specific paths are
  replaced by portable examples ($HOME- and $TMPDIR-relative). Commands, flags, file modes, exit
  codes and limits are unchanged.
  Its acceptance status is in the compatibility record, not here.
  ---------------------------------------------------------------------------------------------
## 0. Isolation from the running v1 runtime: read first

- **Never touch the live v1 paths.** Every scratch path below (registry, discovery files) sits under ONE illustrative
  root, `$EX` = `${TMPDIR:-/tmp}/di-v2-EXAMPLE` (absolute on macOS and Linux).
  - No example names the live v1 runtime directory, its discovery file, or the live registry.
  - Never point any of these commands at them. The parent's office reads that discovery file.
- **The registry is new.** It is a FRESH, DEDICATED one, created by the commands below. It is not the live registry.
- **Interpreter.** Use `python3` (exercised with 3.14.8 and 3.9.6).
- **Working directory.** Run from the root of your Dodging Infinity checkout (the directory holding `dicatalog.py`
  and `dicatalog_http.py`), because the entry scripts import from their own directory.
- **Bytecode.** `-B` keeps bytecode out of the source tree.
- **Absolute paths.** Both CLIs refuse a relative path.

## 1. Launch from the Dodging Infinity source tree

```sh
EX="${TMPDIR:-/tmp}/di-v2-EXAMPLE"   # a fresh scratch root
DI="$HOME/dodging-infinity"       # your Dodging Infinity checkout (any absolute path)

mkdir -m 700 "$EX"
cd "$DI"                          # dicatalog.py and dicatalog_http.py are at its root
```

After the registration in §2, start the loopback HTTP adapter for version 2:

```sh
python3 -B dicatalog_http.py --registry "$EX/registry" \
  --discovery-file "$EX/runtime-v2/backend-discovery.json" \
  --discover-roots --contract-version 2
```

What the adapter does at startup and shutdown:
- **Startup line.** It prints ONE JSON line containing the URL, the routes, the discovery file path, the pid and
  `contract_version` 2. That line never carries the token.
- **Discovery file.** It publishes `{url, port, token}` to the discovery file:
  - the file is 0600, in a 0700 directory created if absent;
  - publishing is atomic and no-clobber, so a second launch on the same path is refused with exit 2;
  - the file is removed at shutdown.
- **Stop.** Stop it with SIGTERM or SIGINT.
- **Runtime source required.** A runtime source is mandatory: `--discover-roots` or at least one `--herd-root PATH`.
  Without one the adapter refuses to start (exit 2).
- **One-shot alternative.** Instead of a listener, one read is
  `python3 -B dicatalog.py query --registry "$EX/registry" --discover-roots --contract-version 2 --wait-seconds 25`,
  which prints one JSON answer line.

## 2. Fresh dedicated two-source registration (exact)

```sh
cd "$DI"
python3 -B dicatalog.py register --registry "$EX/registry" \
  --source-id pixel_live_office --kind mission_core \
  --store-dir "$HOME/.config/dodging-infinity/v2/pixel-live-office/state" \
  --producer pixel-live-office
python3 -B dicatalog.py register --registry "$EX/registry" \
  --source-id di_v2_store --kind workflow_store \
  --store-dir "$HOME/.config/dodging-infinity/v2/store" \
  --producer dodging-infinity-v2
python3 -B dicatalog.py sources --registry "$EX/registry"     # prints the two sources as recorded
```

- **Writes.** Registration writes ONLY `$EX/registry`. Its directory is created 0700 on first registration.
- **Stores are only read.** The stores are never written.
- **Store directories.** The two `--store-dir` values are portable EXAMPLES: use the absolute directories of YOUR
  stores. The `mission_core` store is the directory holding `missions.json` (the absolute `--state-dir DIR` your
  `python3 direquest.py --state-dir DIR ...` commands use; it has no default). The `workflow_store` store is the
  directory holding `workflows.json`.
- **Defaults.** `--max-age-seconds` defaults to 60. These are the exact source ids, kinds and producers that S5
  registered; S5's store directories were machine-specific and are replaced here by the examples above.
- **What was exercised.** S5 ran `register` and `query` (the latter with `--contract-version 2 --discover-roots`) from
  the extracted CANDIDATE backend package of an earlier handoff, not from this source tree. The adapter's v2 launch
  path is covered by the S1 suites. No adapter was launched against the real stores in this gate.

## 3. Launch-time version selection

- **The flag.** `--contract-version {1,2}` on `dicatalog_http.py`, and on `dicatalog.py query|serve`.
  - The **default is 1**, the accepted v1 contract, and the default never changes by itself.
  - **ONE version per process**, for its whole life.
- **No per-request selector.** `GET /catalog?contract_version=2` is **refused (400)**, like any query field other than
  `wait_seconds`. Nothing switches the version at run time.
- **Comparing v1 and v2 takes TWO processes.** Give each its own discovery file; each binds its own ephemeral port:

  ```sh
  python3 -B dicatalog_http.py --registry "$EX/registry" --discovery-file "$EX/runtime-v1/backend-discovery.json" --discover-roots
  python3 -B dicatalog_http.py --registry "$EX/registry" --discovery-file "$EX/runtime-v2/backend-discovery.json" --discover-roots --contract-version 2
  ```

- **Delivery store.** `--delivery-store DIR` is version 2 only. With version 1 it is a usage error (exit 2). Without it,
  Mission State `delivery` is `unavailable`.

## 4. Bounded, read-only limits

**Read routes (all `GET`, nothing else exists):**
- `/catalog`, with an optional `wait_seconds`. It is the only query field, digits with an optional fraction, in
  `(0, 30]`. When absent it defaults to 10.
- `/diagnostics`.
- `/healthz`, which is liveness only, starts nothing and reads no source.

**Access, checked on every request before anything else:**
- **LOOPBACK ONLY: `127.0.0.1`.**
  - There is no flag for another address.
  - A non-loopback peer gets 403.
  - IPv4 only, with no TLS, no tunnel and no public endpoint.
- **Bearer.** A per-launch token from `secrets`. Every request carries exactly ONE `Authorization: Bearer <token>`,
  compared in constant time; otherwise 401.
  - The token leaves the process ONLY through the discovery file.
  - It is never printed, logged, put in argv or read from the environment.
  - A consumer reads it from the file and must not put it in argv or logs either.
- **Host and Origin.** `Host` must be exactly `127.0.0.1:<port>`. `Origin` must be absent or exactly
  `http://127.0.0.1:<port>`. Otherwise 403.
- **Connection limits.** At most 8 connections in progress, and a 10 s timeout per socket read or write.

**Status codes:**
- `200`: every catalog envelope.
- `400`: a malformed request.
- `401`: a missing or wrong bearer.
- `403`: a Host, Origin or peer mismatch.
- `404`: any other path.
- `405`: any other method.
- `413`: an oversized request line or headers.
- No 5xx is ever sent. There is no CORS header and no cookie.

**One in-flight query per owner, busy before cache.** See the consumer contract, §9. Serving v2 does not guarantee live
data on every poll.

**Read budget per refresh.** See the consumer contract, §8.
- **The cap.** The STATED total, `sum_max_query_input_bytes` (4 × 64 MiB with `--discover-roots`, 3 × 64 MiB without),
  is enforced against every read ATTEMPT made in the owner process. Each counted stage holds against its own 64 MiB
  share and the shared total. This is enforcement fidelity, not a new or wider limit.
- **The report.** Exhaustion is reported explicitly in `runtime_capture.refresh_budget.shared_input`, the sources and
  Mission State.
- **SEPARATELY BOUNDED and NOT charged to that budget: the capture subprocess's own reads.** These are bounded by the
  accepted per-root limits inside the 16-root capacity:
  - the runtime listing, through the accepted bounded command;
  - each herd state file, through the hardened 64 KiB state reader;
  - the canonical review preamble, at most 4096 bytes and 16 lines, read one byte at a time.

  The budget is not whole-I/O accounting.

**Review content.** The ONLY review content ever admitted is the canonical review record preamble's **four fields**:
`round`, `reviewer_logical`, `reviewer_agent`, `protocol_token`.
- There is no artifact content and no transcript. Zero transcript bytes are read.
- `gate` / `verdict` / `currency` / `integrity` are facts derived from those four fields and `task.json` linkage; they
  carry no content.

## 5. Where consequential actions live: DOCUMENTATION ONLY

**This backend is READ-ONLY, and that is the governing statement.**
- It has no route, flag or call for any consequential action.
- It does not import, wire, proxy, re-export or invoke any of the entry points below, and it cannot reach them.

They are named only so that a reader knows where consequential actions already live, and does not look for them here.
Each is the repository's own existing entry point, as its own documentation describes it:

| Concern | Existing entry point (not part of this package) | Its own documentation |
|---|---|---|
| Proposing a DI Mission, its durable status, cancelling one's own pending proposal | `direquest.py` (`local_request/`) | `README.md`, `docs/operations.md` |
| Advancing approved DI work (the DI Runtime, a separate local process) | `dirun.py` | `docs/operations.md` |
| The human-facing mobile interface (Grok Bot transport to the Codex Outer Operator; runs nothing itself) | `grokbot.py` (`grok_bot/`) | `docs/grok-bot.md` |
| Terminal intent into the local Codex Operator workflow (never touches Herdr) | `codexgw.py` (`codex_gateway/`); Telegram via `tgop.py` | `README.md`, `docs/operations.md` |
| Commit, push, PR and release delivery | human approval gates only | `OPERATOR_PROTOCOL.md` "Git Delivery" |

Naming a route here does not expose it.
  ---------------------------------------------------------------------------------------------
  READ BUDGETS — FOUR separately bounded categories. Every read is bounded and refused on
  overrun; this is reader-facing accounting, not a limit change, and it is NOT whole-I/O
  accounting: these categories are not a total of all I/O. Per the backend's corrected
  supplement SUPPLEMENT-REGISTRY-BOUND-CORRECTED.b84bbc50 (sha256
  b84bbc50676baea1097dfb3014d717b9be8f314f9f72ba5e97877fcb1539db24; it supersedes 813ce4c6) and
  disposition DISPOSITION-REGISTRY-BOUND-DOC.4612d0d9. Figures are for a refresh WITH discovery:
    1. shared store input: sum_max_query_input_bytes 268435456 = 4 passes x 67108864, ledgered by
       RefreshReadBudget, reported in runtime_capture.refresh_budget.shared_input (section 4:
       4 x 64 MiB with --discover-roots, 3 x 64 MiB without).
    2. registry, SOURCE PASSES: sum_max_registry_bytes 524288 = 2 passes x 262144, reported in
       runtime_capture.refresh_budget. EACH registry read is bounded at 262144 ACCEPTED bytes
       plus ONE DETECTION byte — read only to detect an overrun, never accepted.
    3. registry, COMPOSER load: ONE further load, bounded the same way (262144 accepted bytes
       plus one detection byte), separately bounded and OUTSIDE every reported sum. With
       discovery a refresh makes THREE registry loads (two source passes plus the composer
       load); without discovery, TWO. The reported sum_max_registry_bytes covers the SOURCE
       PASSES ONLY.
    4. capture subprocess: excluded from the shared ledger, bounded separately by the accepted
       per-root limits inside the 16-root capacity. Category 3 complements this exclusion; it
       does not complete a ledger.
  The pass counts DIFFER by category (shared input 4, registry source passes 2): never derive one
  category's figures from another's, and never read a reported sum as covering a load it does
  not name. The registry's own bound is also surfaced as max_registry_bytes in response.limits
  and as max_serialized_bytes in response.registry.

VERSIONS AND HASHES
  This archive's own sha256 is deliberately NOT in this package: those bytes are inside what is
  hashed, so no file here could state it truthfully. It is recorded OUTSIDE the archive — in the
  <archive>.tgz.sha256 sidecar beside it, and in the compatibility record and evidence map
  delivered with it, which also name the catalog contract versions this office reads and the
  accepted backend versions and packages it was checked against.

ACCESS AND PROFILE
  The access file is owner-only. The URL in it carries this launch's token: the token is a
  SECRET, so treat the URL as one (it also reaches browser history). Observation requires the
  token — a connection without it is closed before any office data is sent. Each launch mints a
  new token.
  Profile choice: the state dir is yours to choose; keep it separate per office. Two offices
  must not share one state dir (the second refuses to start). Several browser windows on ONE
  office share that office's single poller — extra windows are not extra polling of the backend.

STOP, REMOVE, RECOVER
  Normal stop: Ctrl-C (or SIGTERM). The office drains, stops and removes its own access file.
  Removing your own package: stop the office, then delete the extracted package directory and,
  if you no longer want its layout and settings, its state dir. Nothing else was installed.
  Restart after a KILLED office (e.g. SIGKILL):
    A normal stop removes the access file; a killed office cannot, so the file stays and the next
    launch from the same state dir REFUSES (a running office is refused the same way). Refusal is
    keyed on the file's PRESENCE only — it is not proof that a live office holds it, and the
    office never guesses with pid or owner checks.
    First make sure the owned Node process has EXITED and no DI office is running from that state
    dir. Then delete <state-dir>/run/browser-access.json yourself and start again. Layout and
    settings (in the state dir) are preserved.
    Then open the NEW URL from the new access file. A browser tab from the killed launch cannot
    resume: its token was minted for that launch only.

WHAT YOU CAN INSPECT
  Missions (objective first; the complete source-qualified refs one step behind), each Mission's
  roles and, where the contract version reports them, its task chains, report facts and review.
  Review: the office shows ONLY the review preamble — four fields — and never the review body:
  `round`, `reviewer_logical`, `reviewer_agent`, `protocol_token`
  The gate (whether a protocol token was parsed) and the verdict are kept apart. A MISSING or
  MALFORMED token is a PARSE OUTCOME — no verdict was read — never a verdict, a rejection or a
  judgement of quality.

READING THE STATES
  unknown vs stale: "unknown" means the backend does not report or does not know the value (the
  literal "unknown"; an unknown count is never shown as 0). "stale" means it WAS observed but its
  freshness has lapsed — a past observation, shown as not live.
  Activity, task, review, verification and delivery are different facts. A role's activity
  (busy, idle, blocked, done, unknown) describes its agent/terminal activity only. "done" is
  TERMINAL ACTIVITY: it is NOT task completion, Mission completion, verification or delivery —
  roles have been observed "done" while their task was not complete.
  Live is decided per response, not promised per poll. served "cached" is neither a liveness
  failure NOR a liveness grant: cached data is live only when EVERY check permits it — the
  envelope, the nested runtime, and the membership. A busy catalog answers served "none" with
  problem catalog_slot_occupied, and a refresh limit answers catalog_refresh_throttled: both are
  NORMAL responses, not faults — the current state is then simply not known, so the office shows
  not live with that reason until a later answer.
  From the backend's consumer contract (V2-CONSUMER-CONTRACT.md, sha256
  eb67dbd1d1c2e30072ee847e96817804a15c7199e3454ec47d8780f53ba01854), VERBATIM, with the ONE token
  correction of ERRATUM-002 (sha256 5e8e7b8a0c02d0731580b5c25b16d6dd826c54e3ced170f5559022258a20c6cb):
  ---------------------------------------------------------------------------------------------
**Report.** `runtime_capture.refresh_budget.shared_input` = `{max_bytes, charged_bytes, reads_refused, exhausted}`.
`max_bytes` equals `sum_max_query_input_bytes`.

Exhaustion is shown where it bites:
- a source reads `unavailable` / `query_input_budget_exhausted`, never fresh-and-empty;
- a Mission State reads `unavailable` / `mission_state_refresh_read_budget_exhausted`;
- a kind reads `unavailable` with `refresh_read_budget_exhausted`.

A refresh that ran out of budget and a refresh that found nothing never share an encoding.

## 9. The request slot: EXISTING accepted behaviour, unchanged by v2

This section states accepted v1 behaviour that v2 retains unchanged. It is not a new claim and not a v2 change.

### Backend fact

The backend supports ONE IN-FLIGHT QUERY per owner.
- **Busy first.** The busy check precedes the cadence and cache check. A busy owner returns IMMEDIATELY: `response`
  null, `served` `none`, problem `catalog_slot_occupied`. This holds even when a still-fresh cached answer is held.
  Busy never falls through to the cache.
- **What counts as busy.** Another request in progress in this owner, an UNCERTAIN query start, or a query worker
  still running after a previous request's wait ended. So a single consumer can also see `catalog_slot_occupied`.
- **Then cadence.** Only once the slot is free does a request inside the 5 s interval return the still-fresh cached
  answer (`served` `cached`), or `catalog_refresh_throttled` [token corrected per ERRATUM-002].
- **Scope.** The slot is per owner, which means per process. There is no cross-process coordination.

### Deployment guidance (NOT a backend constraint)

Our supported office deployment uses one Node consumer per adapter, and multiple browser clients share that Node.
Multiple pollers would be CONSISTENT WITH an alternation of served and slot-occupied answers; no cause is claimed
beyond that. Closing a browser does not close a Node poller.

### Version 2

v2 RETAINS these semantics unchanged. Serving v2 is not a guarantee of live data on every poll.
  ---------------------------------------------------------------------------------------------

ACCESSIBILITY
  Reduced motion follows the operating system's "reduce motion" preference, live. It removes
  motion, never information: characters stay at their seats, arrivals and departures appear at
  once, animations hold still and the camera jumps instead of easing; every state, bubble, label
  and selection still shows.

NOTICES AND UPDATES
  The paths in this section and in CREDITS.txt name files of the BUILT package. In the source
  tree (integrations/pixel-agents/ in Dodging Infinity) the sources are: LICENSE;
  packaging/di/CREDITS.txt; the font webview-ui/public/fonts/FSPixelSansUnicode-Regular.ttf with
  its licence text packaging/di/OFL-1.1.txt. scripts/build-di-package.mjs GENERATES
  THIRD-PARTY-NOTICES.txt and dist/webview/fonts/OFL.txt when it assembles the package.
  Code: MIT (LICENSE). Bundled third-party code: THIRD-PARTY-NOTICES.txt.
  Font: FS Pixel Sans Unicode — SIL Open Font License 1.1, NOT MIT; dist/webview/fonts/OFL.txt
  carries its notices and the licence text (the licence text is TRANSCRIBED — verify it against
  the licence page named in that file).
  Characters: CREDITS.txt (CC0 1.0).
  Updates: there is no auto-update. A new version is a new package with a new sha256; check it
  against the compatibility record, then run it from a fresh extract.

ROADMAP
  This is the Pixel Agents early read-only increment. Munder: withdrawn and unmodified. Controls,
  chat, the native wrapper, Dots and R1: incomplete.
