# Pixel Agents DI office

The Pixel Agents DI office is an early, read-only increment: a pixel-art office in a
browser that **shows** what Dodging Infinity reports about Missions and their roles and,
when the catalog is launched with contract version 2, their task chains, report facts and
review preamble. It lives in its own subtree,
[`integrations/pixel-agents/`](../integrations/pixel-agents/), with its own Node.js
dependency and build boundary.

It observes; it does not control. It does not spawn Herdr, approve Missions, mark Missions
complete, create Git authority, deploy, or rewrite durable state. It is a browser package
only: there is no native wrapper, no signing or notarization, and no controls. Dots (future
and optional, not today's mobile interface) and R1 are separate and are not part of it. Today
the mobile surface is Grok Bot ([docs/grok-bot.md](grok-bot.md)), which hands requests to the
Codex Outer Operator. The office is not the Phase 11 Visual Mission OS of the
[roadmap](roadmap.md#phases-10-and-11), which remains last.

The office has two halves:

- **The catalog bridge**, in Python, part of this repository: `dicatalog.py` (source
  registration and one-shot queries) and `dicatalog_http.py` (a passive loopback HTTP
  adapter), over `mission_sources/` and `target_runtime/catalog*.py`.
- **The office**, in Node.js and TypeScript, under `integrations/pixel-agents/`: a vendored
  Pixel Agents with the DI office changes. Its entry point is `dist/di-cli.js`.

The operator-facing reference that ships inside the office package is
[`packaging/di/README.txt`](../integrations/pixel-agents/packaging/di/README.txt). Provenance,
licences and upstream updates are in
[`integrations/pixel-agents/UPSTREAM.md`](../integrations/pixel-agents/UPSTREAM.md).

## Install and build

Needs Node.js 20 or newer (built and tested with Node.js v26.5.0 and npm 11.17.0) and, for
the bridge, Python 3.9+.

```bash
cd integrations/pixel-agents
git config --get core.hooksPath          # note the result (unset: exit 1, empty)
HUSKY=0 SCARF_ANALYTICS=false DO_NOT_TRACK=1 \
  npm ci --foreground-scripts --no-audit --no-fund --no-update-notifier
                                         # locked install; never a plain `npm install`
git config --get core.hooksPath          # must be exactly what it was before
npm run build                            # dist/di-cli.js and the production webview
```

- **`HUSKY=0` on every install.** The package's `prepare` script runs Husky, which can
  redirect Git hooks. `integrations/pixel-agents/` has no `.git` of its own, so the hooks at
  stake are this repository's. Check `core.hooksPath` before and after every install; if it
  changed, stop.
- **Offline.** With an npm cache that already holds every locked package, add
  `--offline --cache <that cache dir>` so nothing is fetched. This is the form
  [`packaging/di/README.txt`](../integrations/pixel-agents/packaging/di/README.txt) records for
  the package build.
- **No telemetry.** `SCARF_ANALYTICS=false` and `DO_NOT_TRACK=1` stop the analytics
  `postinstall` of one dependency.
- **What stays out of Git.** The subtree's own `.gitignore` keeps `node_modules/`, `dist/`,
  `out/`, test results, Allure output, `*.vsix` and `.env*` out of the repository.
- **The standalone package.**
  `node scripts/build-di-package.mjs --out "$HOME/pixel-agents-di-package"` (any absolute
  directory) assembles the self-contained office package, its `.tgz` and a `.tgz.sha256`
  sidecar from the build above.

Office tests, run from `integrations/pixel-agents/` after the build:

```bash
npm run test:server -- __tests__/di      # DI server suites
npm run test:webview -- test/di          # DI webview suites
npm run test:package-contract
```

The bridge's tests are the repository's ordinary Python suites, for example
`tests/test_catalog.py`, `tests/test_catalog_http.py` and `tests/test_catalog_v2.py`
([CONTRIBUTING.md](../CONTRIBUTING.md#running-the-test-suite)).

## Register the sources

The office reads ONE dedicated catalog registry bound to two sources: the Mission Core store
and the WorkflowStore. Run from the root of your Dodging Infinity checkout. Every path must
be absolute. `EX` is a scratch directory of your choosing; the store directories are the
absolute directories of **your** stores.

```bash
EX="${TMPDIR:-/tmp}/di-v2-EXAMPLE"   # a fresh scratch root
mkdir -m 700 "$EX"
python3 -B dicatalog.py register --registry "$EX/registry" \
  --source-id pixel_live_office --kind mission_core \
  --store-dir "$HOME/.config/dodging-infinity/v2/pixel-live-office/state" \
  --producer pixel-live-office
python3 -B dicatalog.py register --registry "$EX/registry" \
  --source-id di_v2_store --kind workflow_store \
  --store-dir "$HOME/.config/dodging-infinity/v2/store" \
  --producer dodging-infinity-v2
python3 -B dicatalog.py sources --registry "$EX/registry"
```

- The `mission_core` store is the directory holding `missions.json`: the absolute
  `--state-dir DIR` that your `python3 direquest.py --state-dir DIR ...` commands use. It has
  no default.
- The `workflow_store` store is the directory holding `workflows.json`.
- Registration writes ONLY the registry, created 0700. The stores are only read.
- Never point these commands at a live registry or a live runtime's discovery file.

## Launch

Start the bridge, then the office. Both stay in the foreground; stop each with Ctrl-C or
SIGTERM.

```bash
# from the root of the Dodging Infinity checkout
python3 -B dicatalog_http.py --registry "$EX/registry" \
  --discovery-file "$EX/runtime-v1/backend-discovery.json" --discover-roots

# from integrations/pixel-agents/
node dist/di-cli.js --state-dir /absolute/office-state \
  --backend-discovery "$EX/runtime-v1/backend-discovery.json"
```

- **Contract version.** The default is version 1, and it never changes by itself. To read
  version 2, pass `--contract-version 2` to BOTH processes, with its own discovery file
  (for example `"$EX/runtime-v2/backend-discovery.json"`). Each process reads one version
  for its whole life, and a per-request selector is refused. `--delivery-store DIR` is
  version 2 only.
- **Runtime source.** The bridge needs `--discover-roots` or at least one
  `--herd-root PATH`; without one it refuses to start (exit 2).
- **Discovery file.** The bridge publishes `{url, port, token}` atomically and no-clobber as a
  0600 file in a 0700 directory, removes it at shutdown, and refuses a second launch on the
  same path (exit 2). Its startup line never carries the token.
- **Opening the office.** The office prints the path of its access file, not the URL. Open
  the URL stored in `<state-dir>/run/browser-access.json`, or let the
  [`pixel` launcher](#the-pixel-launcher) open it for you. That URL carries a per-launch
  token, so treat it as a secret.
- **One-shot read.** Instead of a listener,
  `python3 -B dicatalog.py query --registry "$EX/registry" --discover-roots --wait-seconds 25`
  prints one answer line.

Recovery after a killed office, profiles, and the full limits are in
[`packaging/di/README.txt`](../integrations/pixel-agents/packaging/di/README.txt).

## World preset

[`packaging/di/worlds/expanded-office.json`](../integrations/pixel-agents/packaging/di/worlds/expanded-office.json)
is an office layout authored for Dodging Infinity. It arranges the existing upstream Pixel
Agents assets already in this directory (furniture, floors, walls and pets), whose
attribution and licences are in
[`UPSTREAM.md`](../integrations/pixel-agents/UPSTREAM.md#licensing). It keeps the original
office's rooms and adds six computer rooms with 24 room computers, plus a connected lobby
whose reception desk carries one more computer. It has no rug or carpet. It is 60 columns by
27 rows, with 161 furniture items and the original two pets.

**This preset is opt-in.** It does not install itself, it does not replace a saved layout,
and it adds or raises no bundled layout revision. Nothing in it writes
`<state-dir>/layout.json` on your behalf: an office with a saved layout goes on loading that
layout, and an office without one still starts from the bundled default.

That is a statement about this preset only, not a promise about upgrades in general. The
office's layout loader (`loadLayout()` in
`integrations/pixel-agents/server/src/layoutPersistence.ts`) can replace a saved layout when a
bundled layout revision is raised, and this preset does not change that.
Keep an exported backup of any layout you care about.

**Adopting it is your explicit choice**, made in the office in your browser:

1. **Back up first.** Settings → **Export Layout**. Keep the downloaded file: it is the
   layout the office is showing now.
2. Settings → **Import Layout**, and pick `expanded-office.json`. In the DI office in a browser
   this opens a paste box instead of a file chooser: open the JSON file in a text editor, copy
   all of it, paste it, and choose **Import** (see *Importing by paste* below).
3. **Reload the office page.**

The import saves the preset as the office's own layout, in `<state-dir>/layout.json`, and
shows it at once. The reload is part of the procedure. Until the page is reloaded, a session
that started on the bundled default can still reflow back to that default world, for example
when the window is resized. After the reload, the saved preset is the layout the office loads.
Later changes in the layout editor save to the same file. If the editor holds unsaved edits,
the import asks before replacing them.

**Going back.** Import the backup from step 1 the same way, then reload the page. If the
office was showing the bundled default when you exported it, the backup is a fixed snapshot
of the default world as it looked then, not the responsive default. To return to the
responsive default, import the bundled `default-layout-1.json` instead, then reload. In a
checkout it is `integrations/pixel-agents/webview-ui/public/assets/default-layout-1.json`; in
the standalone package it is `dist/assets/default-layout-1.json`.

- **Its shape is kept once it is loaded.** A custom layout that arrives when the page loads
  is never reshaped: the office keeps its exact columns, rows and furniture and only fits it
  to the window with whole-number zoom. Zooming by hand overrides the fit. Right after an
  import, before the reload, this does not yet hold.
- **Saving edits.** The preset is within the office's layout limits, so moving a desk in the
  editor saves normally.
- **Where it is.** In a checkout:
  `integrations/pixel-agents/packaging/di/worlds/expanded-office.json`. In the standalone
  office package: `worlds/expanded-office.json`.

### Importing by paste (DI office in a browser)

In the DI office opened in a browser, **Import Layout (paste JSON)** and **Import Ambient
Snapshot (paste JSON)** both open one small paste box. No file chooser is offered there: in
native testing the browser's file chooser would not open a valid `.json` file (Open stayed
disabled). That cause was **not** diagnosed, and the paste box is an alternative supported
route, not a repair of the chooser.

- **Layout.** Pasted text goes through exactly the checks an imported file goes through: at most
  104 MiB of UTF-8, checked before parsing; then the layout limits; a refused layout changes
  nothing. When it is accepted the office shows it at once and sends the save; the message says
  the save was sent (it is queued while disconnected) and does not claim a confirmed write.
- **Ambient snapshot.** Settings → **Export Ambient Snapshot** downloads one file,
  `pixel-agents-ambient-<N>-aboard.json` (N = residents riding a train when exported). Pasting it
  back restores the decorative residents and trains of the SAME world and resident set: at most
  65,536 bytes, checked before parsing; a snapshot from another world or resident set, a
  malformed one or one over the bound is refused whole, with the reason shown, and nothing
  changes.
- Outside the DI office (the standalone Pixel Agents surface) **Import Layout** still uses the
  file chooser as before.

### The forest and connected-world presets

Two larger presets follow the same opt-in procedure: back up with Export Layout, Import the
preset, reload the page. Like the expanded office, neither installs itself, replaces a saved
layout or raises a bundled layout revision.

- [`vast-forest.json`](../integrations/pixel-agents/packaging/di/worlds/vast-forest.json): the
  palace placed whole inside a 176 by 112 world of woodland, trails, a pond and an observation
  deck. Its art is drawn in code by this project.
- [`metropolis.json`](../integrations/pixel-agents/packaging/di/worlds/metropolis.json): the
  connected world, 352 by 224. The forest world sits unchanged in its top-left corner, and eight
  districts surround it: the Metropolis, the North Shore, the Autumn Wildwood, Mirror Lakes, the
  Silver Pine Highlands, the River Valley Farmland, the Harbour and the Aquarium Quarter. Roads,
  trails, bridges and boardwalks join them. Water is never walkable.

**Getting around.** These controls move the camera only.

- **Camera views** (top left). Office frames the palace, the home of the office's agents. Deck
  frames the observation deck and the view north. Woods shows the middle of the world at the
  smallest pixel-perfect zoom; it is a framed region, not an overview.
- **Districts.** The readable list alternative to the Map. It names every district and goes to
  one. With the keyboard: Tab to Districts and press Enter, Tab to a name and press Enter. Esc
  closes the list, and a status line announces where the camera went.
- **Map.** Shows the whole world with district labels. Click a spot to go there at the office
  zoom. Esc closes it.
- **Panning and reduced motion.** Arrow keys pan. Manual zoom or pan takes over until a view is
  chosen again. With reduced motion set, the Map refreshes less often.

**Static art, and where animation comes from.** The legacy art baseline is static: every
sprite extracted for the connected world is one frame, never animated, and so are the woodland
variants and the lounge seating. In that art set the only animated original companions are the
two pets that the office already had. That describes the artwork, not what moves at runtime: in
the DI office, ambient residents animate on whatever layout is loaded, the legacy presets
included, and trains run where the loaded world contains a subway line (of the bundled presets,
only the living metropolis). The inherited decorative animal and vehicle artwork stays static.
Decorative animals, vehicles, companions and residents never create agents and never imply
Mission work. Per-sprite provenance (source atlas, its hash, the source rectangle) is in
[`ASSET-PROVENANCE.json`](../integrations/pixel-agents/packaging/di/ASSET-PROVENANCE.json).

**The living metropolis (opt-in).**
[`living-metropolis.json`](../integrations/pixel-agents/packaging/di/worlds/living-metropolis.json)
is 352 by 312 (109,824 tiles): the connected world extended south with two further office
complexes, a Gothic cathedral quarter, retail and dining destinations, and a subway. Import it
like the other presets (Settings, Import Layout (paste JSON), then reload). In current use:

- **Ambient residents and jobs.** A decorative local population walks routines while the office
  is open, even with no real agents: in this world, 145 residents (140 ordinary, with jobs such
  as bellhop, police officer, firefighter, store clerk and barista, plus five Easter-egg
  families). They are labelled Ambient and decorative, are never agents, never take a seat, and
  a real agent always wins selection and seating.
- **Subway.** Four stations and two trains; residents board at one station and alight at
  another. The Stations list moves the camera only: a jump is instant camera travel, not a ride.
- **Saving and moving the simulation.** While open, the office saves the residents' and trains'
  state (identities, routines, journeys in progress) under its own state folder. Settings,
  Export Ambient Snapshot downloads it; Import Ambient Snapshot (paste JSON) restores it. A
  snapshot from a different world or resident set, or over 65,536 bytes, is refused whole and
  nothing changes.
- **Viewer closed.** Nothing runs while the office page is closed: no simulation time passes. A
  hidden tab is caught up by at most 20 seconds when it is shown again.
- **The glow.** An agent glows, with a soft pale-gold aura around its body, only while the
  catalog reports it working at its terminal with fresh data. Idle, unknown, stale, expired and
  disconnected agents never glow, and residents never glow. Reduced motion makes it steady. A
  glow is not evidence of task, Mission or delivery completion.
- **Passive.** Observation only: no dispatch, no model invocation and no change to the canonical
  catalog.

**Bounds.** The office admits any layout within these limits, from every path a layout enters
by:

- grid: up to 384 tiles on each axis, and at most 110,400 tiles in all (the living metropolis is
  352 by 312 = 109,824);
- objects: 16,384 furniture items, 64 pets and 64 areas;
- carpet: 4,096 carpeted tiles;
- distinct colour variants: 1,024 tile, 256 carpet and 1,024 furniture;
- bytes: a saved layout file over 104 MiB and a save message over 64 MiB are refused.

The colour and carpet caps bound one layout. The office also clears its rendering caches
whenever a layout is loaded or imported, so they cannot grow across imports.

**Still read-only.** These presets change what the office draws, not what it can do. The
office stays the home of the agents the catalog actually reports. The DI, Grok and Codex
action routes are unchanged, and the world adds no orchestration, dispatch, messaging, shell
or execution route.

## The `pixel` launcher

[`packaging/di/pixel`](../integrations/pixel-agents/packaging/di/pixel) opens the address
that a running office recorded, in your default browser, without printing the address or its
token.

- **The command is lowercase `pixel`.** "Pixel" is only how the office is spoken of. File
  names are case-sensitive on Linux, so `Pixel` is not found there.
- **Where it is.** In a checkout: `integrations/pixel-agents/packaging/di/pixel`. In the
  standalone office package: `pixel`, at the package root.
- **What it needs.** Python 3 (standard library only), and macOS (`/usr/bin/open`) or Linux
  (`xdg-open`).
- **Putting it on your PATH.** Run it by path, or link it yourself into a directory on your
  PATH, for example from the checkout root:

  ```bash
  BIN_DIR="$HOME/bin"          # set this to a directory on your PATH
  ln -s "$PWD/integrations/pixel-agents/packaging/di/pixel" "$BIN_DIR/pixel"
  ```

  Without `-f`, `ln -s` refuses to replace an existing `pixel`.

**Tell it which office.** You give it the office's state directory, never its token or URL.
The first of these that is present wins:

1. `pixel --state-dir /absolute/office-state`
2. `PIXEL_AGENTS_DI_STATE_DIR=/absolute/office-state`, in your environment
3. a user-local file, `${XDG_CONFIG_HOME:-~/.config}/pixel-agents-di/pixel.json`:

   ```json
   {"state_dir": "/absolute/office-state"}
   ```

The directory is the absolute `--state-dir` the office was started with. Keep this
configuration out of Git. `pixel` only reads it and never creates it.

**What it does.**

1. It reads the office's own access file, `<state-dir>/run/browser-access.json`. The
   office writes it at launch as `{url, port, token}`, owner-only.
2. It checks the file's shape: `url`, `port` and `token` are present and of the right
   type; the `url` is plain `http` on `127.0.0.1` with a port and no embedded credentials;
   the `url` carries the file's `token`; and the `url`'s port is the file's `port`.
3. It checks that something accepts a TCP connection on that loopback port.
4. It hands the recorded address to the browser opener. On success it prints what it did and
   what it did not check.

It never starts the office, the backend, a model or a Mission. It installs nothing and writes
nothing. It never prints the address or the token: each message names the cause instead, and
the opener's own output is discarded because it can echo the address.

| Exit | Meaning |
|---|---|
| 0 | The recorded address was handed to your browser |
| 2 | Usage error |
| 3 | No office configured: no `--state-dir`, no environment variable, no config file |
| 4 | The configuration is invalid: not an absolute directory, or an unreadable or malformed config file |
| 5 | No access file: no office is running from that state directory, or it is the wrong directory |
| 6 | The access file is unreadable |
| 7 | The access file is not JSON |
| 8 | The access file's `url`, `port` or `token` is missing or of the wrong type |
| 9 | The address was refused: not `http`, not `127.0.0.1`, no port, or embedded credentials |
| 10 | The address does not carry the access file's token |
| 11 | The address's port is not the access file's port |
| 12 | Nothing is listening on that port: no office is running, and the access file is stale. A killed office leaves it behind, with a token for a launch that is gone; see `STOP, REMOVE, RECOVER` in `packaging/di/README.txt` |
| 13 | The browser opener failed, is missing, or the platform is not macOS or Linux |

**Limits.**

- **Nothing is authenticated.** The shape checks are not authentication, and liveness is
  only a TCP connect to a loopback port. `pixel` does not identify the process that is
  listening and does not validate the token. If the office was killed and another program
  now listens on its old port, the stale address is opened anyway, and the page will not be
  the office.
- **The token reaches other processes.** The address, token included, is an argument of the
  opener process while it runs, and the browser receives it. While the opener runs, anything
  that can list this machine's processes can read it; on most macOS and Linux systems that
  includes other users' processes. Browser history keeps it too. `pixel` keeps it out of its
  own output only.
- **It does not check that the page loads.**
- **A slow opener.** If the opener is still running after 15 seconds, `pixel` reports the
  hand-off and returns without waiting for it.
- **Platform coverage.** It was exercised on macOS with the macOS opener replaced by a
  recorder, so no real browser opened. The Linux branch was exercised on macOS with a
  stand-in `xdg-open`, not on a Linux host.

## Architecture and trust boundary

```text
stores (read only) ─┐
herd state files ───┼─> catalog bridge (Python) ── GET only, 127.0.0.1, bearer ──> office (Node) ── 127.0.0.1, per-launch token ──> browser
herdr agent list ───┘     dicatalog_http.py                                          dist/di-cli.js
```

**The bridge reads; it does not write.**

- **What it reads, contract version 1 (the default).** The registered stores, through each
  store's own load validation, and each captured herd root's three fixed state files
  (`task.json`, `runtime.json`, `role-bindings.json`), through the hardened bounded reader.
  It runs one read-only `herdr agent list`. Version 1 admits no review content and follows no
  children. Every read is bounded.
- **What contract version 2 adds.** A fourth fixed state file at every captured root, the
  children record `children.json`, read through the same hardened reader. It follows that
  root's recorded children for its current task ONE level down (depth 1). A child root is
  only a path a validated record names, validated like a discovered root, read tolerantly,
  and inside the same root capacity. A child's own recorded children are counted, never
  captured.
- **Review content, version 2 only.** The only review content admitted is the canonical
  review record's preamble, reached from the task record's own reference through the
  expected path `.herd/state/reviews/<task_id>-round-<NN>.md`. It reads at most 4096 bytes
  and 16 lines, one byte at a time, and stops before the `## Transcript` line, so no
  transcript byte is read. Exactly four fields project: `round`, `reviewer_logical`,
  `reviewer_agent`, `protocol_token`. The one other review read is the round's
  reviewer-declared findings record, a sibling file written by the local review gate; see
  [Reviewer-declared findings](#reviewer-declared-findings). It is not the transcript either.
- **What it writes.** Registration writes only its registry. The adapter writes only its own
  discovery file. Neither ever writes a store.
- **Routes.** `GET /catalog`, `GET /diagnostics`, `GET /healthz`, `GET /artifact` and
  `GET /code_review`, and nothing else. `GET /artifact` returns one recorded artifact's text for
  the evidence library. It takes an admitted reference (Mission, artifact id, listing cursor),
  never a path. `GET /code_review` returns the read-only view of an active assignment's workspace
  (see [Live code review](#live-code-review)); it takes a Mission and a task id, and for one
  file the snapshot and file id the overview listed. Never a path, a ref or a git option. All
  routes are loopback-only (`127.0.0.1`), behind one bearer token compared in constant time,
  with exact `Host` and `Origin` checks. There is no CORS header, no cookie and no 5xx.
- **No route to action.** It imports, proxies and invokes none of the consequential entry
  points (`direquest.py`, `dirun.py`, `grokbot.py`, `codexgw.py`, Git delivery).

**The office only reads the bridge.**

- **What it reads.** It reads the bridge's discovery file and polls `GET /catalog`. It never
  starts, supervises or restarts the bridge; a missing discovery file is shown as
  unavailable.
- **What it serves.** It binds `127.0.0.1` only and serves the production webview and one
  WebSocket. A socket without the per-launch token is closed before any office data is sent.
  There is no hook route, no consent flow, no terminal or launch path, no model invocation
  and no external asset directory.
- **What it writes.** Only its own state directory: settings, layout, seats and its access
  file.

**What it shows is what was reported.**

- **Unknown is not zero.** "unknown" means the backend does not report or does not know the
  value, including a model; it is never shown as 0 or guessed.
- **Stale is not live.** "stale" means the value was observed but its freshness lapsed.
  Liveness is decided per response.
- **Activity is not completion.** A role's activity, such as "done", is terminal activity,
  not task, Mission, verification or delivery completion.

### Reviewer-declared findings

A Reviewer's verdict is the round's protocol token, `HERD_DECISION: APPROVE` or
`HERD_DECISION: REJECT`. The Reviewer may also DECLARE public findings in its own output. The
local review gate validates that declaration against the routing context it recorded, and
stores it beside the round. The evidence library shows it.

**Three different things.** Never treat one as another.

- **The canonical gate.** The supported `herdctl review-decision`. It records the round and the
  verdict. The verdict always comes from the protocol token.
- **The isolated candidate proof.** This checkout's writer, run by
  `tests/review_findings_proof.py` against a saved route and capture, inside an empty scratch
  directory. It is a local test. It writes nothing anywhere else.
- **Installed-producer compatibility.** Whether the `herdctl` your Pod runs carries this
  writer. Until it does, the canonical gate records the verdict only, and the library shows that
  round's findings as **not recorded** (`findings_legacy_record_predates_field`).

**The process.** Route, then declare, then capture, then validate and persist.

1. **Route, before the request.** On an ACTIVE task, run the route command (below) with the
   SHA-256 of the candidate under review. It records the routing context in the task: a
   single-use 16-hex nonce, the task, the NEXT round, the reviewer's logical name and agent, and
   the candidate. It prints that context and sends nothing.

   <!-- findings-route-command:begin -->
   ```text
   herdctl review-findings-route --repo <repo> --reviewer reviewer1 --candidate-sha256 <64-hex>
   ```
   <!-- findings-route-command:end -->

2. **Declare.** Give the Reviewer the nonce, task id, round and its logical name, and describe
   the format below in words. Never paste marker lines or a filled envelope into the request.
   A BEGIN or END line echoed from the prompt is captured too. It is stray envelope text, and it
   makes the round refuse. That has been observed: an orphaned END line from a pasted template
   refused a real declaration. Writing output is the Reviewer's only act. It stays
   read-only/never.

   The compact form (version 2) has whole lines of at most 88 characters. The candidate is NOT
   on the wire: the stored route pins it, and the echoed nonce binds the declaration to that
   route. A finding is one or more numbered parts, `[F<finding> <part>/<parts>]`, joined by
   exactly one space into `severity | path[:line] or - | summary`:

   <!-- findings-example:begin -->
   ```text
   HERD_FINDINGS_BEGIN v2
   nonce 5f0c2a9e41b7d3c8
   task 20261017-133442-1bfec8
   round 16
   reviewer reviewer1
   count 2
   [F1 1/2] blocker | target_runtime/review_findings.py:235 |
   [F1 2/2] a fenced example envelope is promoted as a real declaration
   [F2 1/1] non_blocking | - | the request must not echo envelope marker lines
   HERD_FINDINGS_END v2 nonce=5f0c2a9e41b7d3c8 count=2 parts=3
   ```
   <!-- findings-example:end -->

   - **Severity** is `blocker` or `non_blocking`.
   - **Limits:** at most 32 findings and 8 parts per finding. A summary is at most 280
     characters, a path at most 200, and every line of the compact form at most 88. A path
     must sit whole within one part.
   - **Empty:** `count 0` with no part lines declares NO findings.
   - **Long form:** the version 1 form (one `[F<n>] … [/F<n>]` line per finding, with a
     `candidate` line) is also accepted. One route takes one declaration.

3. **Capture at full native idle, through supported controls only.**
   1. Read the Reviewer only once its status is idle or done.
   2. Record the original view first, with `herdr pane layout --pane <pane>` and
      `herdr pane get`.
   3. Zoom the Reviewer's pane with `herdr pane zoom <pane> --on`, then read with
      `herdr agent read <agent> --source recent-unwrapped --lines 500`.
   4. Unzoom with `herdr pane zoom <pane> --off`. **`zoom --off` alone does NOT restore focus**:
      zooming moved focus to the Reviewer's pane, and unzooming left it there. Restore focus
      with herdr's supported directional focus moves, one step at a time. Then confirm with
      `herdr pane layout --pane <pane>` that `zoomed` is false, `focused_pane_id` is the
      original, and every pane's rectangle equals the recorded original.
   5. Do this restoration and check after a failed attempt too.

   **Observed capture shape.** On this Pod's Reviewer pane, at full native idle, zoomed:
   - 44 lines;
   - at most 95 Unicode characters per line;
   - a finding line wrapping at about 93 characters, counting the TUI's 2-character
     continuation indent.

   Unzoomed, the same pane is 24 columns wide and keeps 21 lines. These are observations under
   those conditions, NOT a universal capacity. Measure in characters, not bytes. `wc -l` counts
   newlines, so it is one short when there is no trailing newline. `awk length` counts bytes,
   and the TUI's decoration (`·`, `—`, `•`, `…`, `›`) is 2 or 3 bytes per character. Those two
   mistakes once read 43 lines at 100 for this capture.

4. **Validate and persist.**
   - **Canonical gate:** `herdctl review-decision --repo <repo> --reviewer reviewer1`, once the
     installed producer carries this writer.
   - **Isolated candidate proof:**

     <!-- findings-proof-command:begin -->
     ```text
     python3 tests/review_findings_proof.py --route route.json --capture capture.txt --out <empty dir>
     ```
     <!-- findings-proof-command:end -->

   The writer consumes the route, then reads ONLY an envelope that echoes its nonce. It ignores:
   - anything inside a fenced code block, which is an example, never a declaration;
   - quoted lines;
   - prose;
   - envelopes echoing another nonce.

   It then checks the declaration's task, round and reviewer against the stored route. When
   valid, it writes `<task>-round-<NN>.findings.json` beside the round file, plus the task's
   `last_review_findings` reference (state, file, SHA-256, count). The reader opens that one
   file by its expected name, checks the digest, re-validates it with a closed schema, and binds
   it to the round's reviewer and preamble.

**What a round's findings say.**

| State | Meaning |
|---|---|
| **not recorded**, legacy | The round predates the field, or the producer has no writer. Nothing is reconstructed. |
| **not recorded** | The round was not routed, no envelope echoed the route, or the only one was inside a code example. |
| **refused** | A declaration or its record did not hold: wrapped, truncated, incomplete, duplicated, reordered, miscounted, over a bound, wrong task, round or reviewer, or a damaged reference or record. The code says which. Nothing is ever partly kept. |
| **empty** | A complete, valid declaration of `count 0`: the Reviewer declared NO findings. It never means that capture lost them. A capture that lost a declaration reads as refused or not recorded, never as empty. |
| **present** | A complete, valid declaration, persisted and re-validated, shown exactly as declared. |

**Provenance ceiling.** Findings are model-authored text that the routed Reviewer's own output
declared. They are bound to the task, round, reviewer and candidate by the single-use nonce the
gate issued BEFORE the Reviewer saw the request. That is not a cryptographic proof of reviewer
identity. Findings are metadata, NOT approval authority. They never change the verdict, which
governs.

**Never done.**
- Guessed rejoining of wrapped or truncated lines.
- Mining transcripts or historical logs.
- Permission, model, pane-configuration or installed-producer changes.
- A blanket production cutover.
- A route to action from the world.
- A new approval loop.

### Completed Missions: historical reads

A COMPLETED Mission has no live team, so the roster lists it as `not_applicable /
mission_not_active`, and no team is ever invented for it. Its recorded artifacts can still be
read, under the execution its canonical record proves. This is a separate, explicitly
**historical** basis. It never replaces the active-binding rule for a running Mission.

**When a completed Mission's recorded execution counts as established.** The bridge establishes
an execution identity ONLY for a Mission Core record that is `COMPLETED` and has all of these:
- a verified run;
- a recorded intent;
- a receipt naming the task;
- a PREPARED workspace binding whose digest re-derives and agrees exactly with the intent.

Anything missing gets its own code. An older record with no prepared binding is
`execution_prepared_binding_absent`: it is never upgraded, and nothing is backfilled.

**What the identity covers.** The source registration, the revision, the state, the whole run and
the whole lifecycle chain. The listing names a historical cursor that binds the journal head AND
that identity. So a run, lifecycle or registration change refuses the read even when the evidence
journal did not move, and a read is never answered under a newer identity.

**Checked twice: the registry AND the record.** Before admission and again after the file read,
before anything is released, the bridge re-reads the CURRENT source registry. It also re-reads the
record through Mission Core's own load validation. Each time it requires:
- the Mission's source and every observing source registered exactly as the listing named them;
- no source universe other than the listed one;
- the identity unchanged.

A source added, removed or changed since the listing is one that uniqueness was never shown over,
so the read is `historical_uniqueness_unverifiable` until a refresh lists it. A removal, repoint or
observer change during the read discards the content. The owner does not have to refresh for any of
this to be seen.

A bound Mission's read also re-reads the registry before release: its own source must still be
registered exactly as it was when the read was admitted.

**Within the same budget.** The second registry read fits inside the unchanged per-request cap.
Before the file is read, the remaining budget must cover the file and both re-reads at their bounds,
or the request is refused there (`request_read_budget_exhausted`), before any content byte. That
holds whenever the first registry and store reads together are at most one store bound plus one
byte.

**The panel keeps two things apart.** It shows the recorded execution ("Read under: HISTORICAL
recorded execution … No live team") separately from the listing's freshness. A disconnect, an
expiry or a moved identity hides read content, exactly as for an active Mission.

**One execution, two source kinds.** A registered `task_native` source may observe the completed
Mission's own herd root. Its row is NOT a competing claim only when all of these hold:
- its model is `herdr.task/1`;
- its recorded schema is `di.catalog.herdr_task` version 1;
- its identity source is `herdr.task_json`;
- its task AND exact workspace both agree with the canonical row;
- `task.json` names that root;
- its status is `COMPLETE` or `COMPLETED`.

It supplies no read authority, and it can never repair a missing canonical fact.

These still refuse, as `historical_identity_conflict`:
- the same task in another workspace;
- the same workspace with another task;
- an inconsistent or ambiguous observation;
- other provenance;
- a contradictory status;
- any second Mission Core or Workflow claim, even with an identical task and path.

A partial or unavailable source refuses as `historical_uniqueness_unverifiable`.

**The legacy capability limit (stated exactly).** Root's read-only locator inventory of the four
existing completed Missions found every recorded artifact beneath `.herd/evidence` or
`.herd/state`:

| Mission | Recorded artifacts |
|---|---|
| `living_world` | 9 |
| `massive_world` | 8 |
| `pixel_live_office` | 9 |
| `vast_forest` | 6 |

What this means for those Missions:
- They are browsable for references and metadata.
- Zero production-artifact-body admission is demonstrated or promised for them. Every recorded
  locator is private, so no body of theirs can be admitted.
- A body that reaches the disclosure check is refused as `locator_in_private_location` before
  any byte is read. That check is pure: it looks only at the locator, never at the filesystem.
- A body that fails an EARLIER check keeps that check's own code. It is never folded into the
  private-location refusal. The checks run in this order:
  1. the listing's freshness, then the Mission's identity, cursor and uniqueness;
  2. the source registration;
  3. the store and its journal;
  4. the artifact and its locator;
  5. the declared class;
  6. disclosure.

  For example, an artifact that no contract pin or accepted verifying evidence declares is
  `artifact_class_not_declared`. A record that does not establish the execution identity is
  `historical_identity_not_established`.
- Root reports that the canonical identity facts of these Missions exist. The private-location
  refusal is the disclosure policy working, not missing history.
- A recorded link is necessary, but it is never disclosure authority. Completion is not a
  disclosure grant.

**How a completed Mission's artifact becomes readable.** It must already be a declared, public,
text-format `REPOSITORY_PATH` artifact with its digest. It must be recorded BEFORE completion,
because Mission Core refuses result writes on a terminal Mission. It must be declared by the
approved contract or by accepted verifying evidence. Nothing relocates legacy artifacts, and no
digest is ever reused for a new locator.

**Proof labelling.** The isolated tests build a completed Mission through Mission Core's normal
APIs. Their seeded lifecycle facts, isolated principals and supplied inputs are TEST DATA: no real
dispatch, no real execution success, no independent production verification and no human
authorization. They are never production artifact admission. The browser proof on real Missions
is Root's.

### Live code review

Selecting a live agent whose assignment is **active** offers **Open code review**. The side panel
is only the entry: the reading happens in a large dialog (full screen on request) with an
overview, a searchable tree of the changed files and a unified or side-by-side diff. It is view
only. It never edits, stages, applies, reverts, commits, dispatches, approves, opens a shell or
sends anything but a read.

**Who is offered it.** A live member of the DIRECT task of a Mission Core Mission whose standing
is `active` and whose binding is `bound`. This is a different question from the evidence
library's: a COMPLETED Mission is never offered a live review, a plain Herdr task is not a Mission
Core record, a child-task member does not work in the Mission's workspace, and a v1 office
corroborates no workspace. Where it is not offered, the inspector says why in words. The target
comes from the canonical record: the renderer never supplies a root, a ref, a pathspec or a git
option, and similar names or paths are not identity.

**What is compared, exactly.** The working tree of the assignment's own worktree against the
commit its `HEAD` names (branch or detached HEAD, the commit's object id shown), as observed at a
stated time.
- **Covered:** staged changes, changes not yet staged (together, as the net difference from
  `HEAD`; a file changed both ways is one entry marked "staged, then changed again"), new
  files git does not track yet within the reader's bound, and **the commit a submodule points
  to**: a submodule whose recorded commit changed is one entry marked `submodule`, shown as its old
  and new commit. Git is told not to look inside a submodule (`--ignore-submodules=dirty`), so
  nothing in a submodule's own configuration can run, and work there that is not committed does not
  appear. An untracked directory that is itself a repository is one `not shown` entry that says so
  (its files are not part of this repository's changes).
- **An end-of-file marker is not a line, and belongs to the line before it.** "No newline at end of file" never
  counts as a line and never truncates a file by itself, so exactly 6,000 lines with no final newline is a
  complete diff, not a truncated one. Its characters count against the character bound, and it is kept or cut
  TOGETHER with its line: a line is kept only if it and its marker fit, so at the bound the marker is never
  dropped while its line stays. A line cut with its marker is a truncation like any other and is said ("Showing
  the first N of M lines").
- **An empty listing is a finding, not a verdict.** When nothing is listed the dialog says "No changes found",
  that no staged, unstaged or new files differ from the commit **as far as this view can tell**, and that this
  is **not proof the working tree matches it**: a private file is never opened, so whether one changed is judged
  from recorded metadata and an edit of the same size within the recorded second may not be seen. The header's
  count reads "no changes found" for the same reason, not "0 files changed".
- **Focus and keys stay inside the dialog, across a read as well as between presses.** The page behind is inert
  and Tab wraps inside the dialog; Escape closes it; `[` and `]` move between files; none of those keys reaches the
  office. A control can stop being focusable while it holds focus (Refresh is disabled for as long as a read is in
  flight, "Smaller text" at its smallest size), and the browser then drops focus to the page body, where the next key
  would be dispatched OUTSIDE the dialog and reach the office. So focus that falls out of the dialog is brought back
  to it, and a key that is dispatched outside it anyway is taken before anything behind can see it and handled
  as if it had arrived inside. The browser regressions watch focus on every animation frame and every key across the
  transition, because a check made once after a key cannot see a gap between two keys.
- **Renames** are shown when git pairs a removed and an added file; otherwise they are a removal
  and an addition. A new file takes part in no pairing. Git pairs by reading both bodies, and a
  private body is never read, so a file moved from a private name to a public one is paired from
  its blob id alone: moved unchanged, it stays withheld; moved and also changed, it is a withheld
  removal and an ordinary addition.
- **Not covered, and said so in the dialog:** ignored files, what is inside a submodule, the
  content of binary or non-UTF-8 files, files over a bound, and private locations (listed by name
  only).
- **A shared worktree shows the team's changes.** Nothing says who made a change; the agent a
  review was opened from is never credited with it.
- **Content filters** (git-lfs, a clean/smudge pair) a repository configures are NEVER run: not
  those listed when the review is opened, and not one that appears in the repository's
  configuration afterwards. The dialog names the ones it found, and a file one manages is compared
  as stored, so it can look changed.

**Nothing is written to the workspace, measured.** `git diff` rewrites `.git/index` (even with
`--no-optional-locks`) and `git status` does too unless optional locks are off, so neither
`git diff` nor a default `git status` is ever pointed at a live workspace. The reads are
plumbing (`rev-parse`, `diff-index`, `status --no-optional-locks`, a config read) under a fixed
environment, a literal-pathspec pin, a wall-clock deadline and no external-diff, textconv or
filesystem-monitor program. `tests/test_code_review_git.py` digests the target's whole git state
(the worktree's git directory and the common directory) before and after every verb, on a plain
repository and a linked worktree with a stat-dirty index, beside controls that DO rewrite it.

**Nothing the repository configures is run, measured.** Git runs a content filter's clean program
whenever it re-hashes a file whose stat no longer matches the index, and it can only be switched
off per filter *name*, so a driver added to the repository's configuration after the names were
listed would not be covered (an earlier build of this view did exactly that, and a marker program
showed the race). A read that can hash a working file (the listing, the counts, one file's diff)
therefore never reads the repository's configuration: it runs in a git directory of its own
making, holding a configuration the read wrote (only file-mode, line-ending and stat settings are
carried over, each in its exact shape), a detached HEAD, a COPY of the index with its modification
time (git's racily-clean rule depends on it), and the target's object store borrowed through
`objects/info/alternates`. There is no driver in it to find, and nothing git writes can land in the
target. The few reads that still use the repository's own configuration (`rev-parse`, a config
listing) were measured with a marker program behind every key that names a program, and ran none.
A repository that uses a split index, another object format, or has an index over 64 MiB is
refused rather than read. The measurements are kept with the item-3 evidence
(`filter-race-probe.txt`); `git --attr-source` would be another route but needs git 2.40, and this
machine has 2.39.5.

**Side by side is two panes.** The old side (headed `Base (old)` with the base commit) and the new
side (headed `Working tree (new)` with the time it was observed) are two equal-width reading
regions, each with its own sideways scrollbar (drawn, not an overlay that appears on use) and
held at one vertical position so a removed line stays opposite the line that replaced it. A long
line scrolls on the side it is on, up to the clipped length shown (1,200 characters), and says so
in that side's heading; the line number and the `+`/`−` mark stay at the pane's left edge while it
does. Neither side is ever pushed past the dialog's edge, and the page itself never scrolls
sideways. Two columns are offered from 1,040 px of dialog width. Below that the toggle is disabled and
the reason is TEXT on the diff's toolbar row (`Side by side needs a dialog at least 1,040 px wide: widen
the window or use full screen.`), not only the toggle's tooltip, so a keyboard or touch reader is told too;
a phone-width window, which has no layout choice and no full-screen button, says `One column at this
width: side by side needs a wider window.` Either way the unified view is shown, and the reader's choice
is kept for when the window is widened.

**Withheld, never read.** A path under git's own directory, Herdr's private state, agent state or
credential directories, a hidden name this view does not list, a credential-like name or key
material is listed by name with no counts and no content. And no git read that could hash or diff a
file is ever pointed at one: before any such read the tracked NAMES are read (from the index and the
tree, which open no working file), every private location among them is decided by the one policy,
and the status listing, the counts, the modes and a file's diff are told to leave those locations
out in git's own pathspec (each spelled as literal text, never a pattern), so git does not visit,
stat or hash anything under one. All of an observation's reads go through the same private copy of
the index, so what was decided private is what they leave out, and it is checked to be unchanged at
the end; a file's diff is read through that same copy, names its path as a FILE (nothing under it
is selected, so a file that has become a directory is not expanded), and is refused before git is
asked if the path is no longer the kind of thing it was listed as. The name and kind of change of a
withheld tracked file come from reads that open no file: what is staged, from the index against
`HEAD`; what differs in the working tree, from the index's own RECORD of the file (its mode, its
size and the second it was recorded) compared by this program with the file's stat. Git is never
asked to compare a working file for it, so no recorded time, however far ahead of the index's own,
can make a file be read (git's fallback of hashing a "racily clean" file is not reached). **That is
a metadata-only inference, and the dialog says so** (in a withheld file's words and in "What is
compared?"): a withheld file that was only touched is listed as changed; an edit of the SAME SIZE
within the recorded second is not seen, so a private file that did change can be shown as unchanged
(it under-reports; it never reveals). The record also carries the modification time's nanoseconds;
they are available in git's output and are NOT used, because this build of git compares an entry at
the second as well (measured: a same-size rewrite 60 ms later is not seen by git's own comparison of
a non-racy entry either), so a stricter comparison would list as changed what git itself lists as
unchanged. Whether the precision should be tightened is a decision for review, not foreclosed here.
A destination that took its privacy from its source (an unchanged file moved from a private name to
a public one) is left out of every read from the start, like the source. Other stated consequences:
a private location that holds a tracked file is
left out whole, so untracked files inside it are not listed; and a private file moved to a public
name AND changed is not recognised as a move (see Renames). Git's own walk of untracked directories
still reads any `.gitignore` it meets, including inside a private location that holds nothing
tracked; nothing else under a private location is opened. A FIFO planted where git keeps its
index, `info/exclude` or `info/attributes` is never waited for: a non-regular index refuses the
read, a non-regular info file is skipped.

**Movement is the normal case.** The overview is observed twice and must agree with itself. It
carries a snapshot; a file read is refused (`snapshot_moved`) when the workspace is no longer that
snapshot, and the dialog then labels the earlier overview **Out of date** and dims it. Before any
answer is returned the registry, the catalog's answer and the workspace are read again; one that
moved is discarded. When the connection, the office state, the assignment or the agent can no
longer be vouched for, the dialog **clears** the review and says why; it does not come back by
itself.

**Bounds (never raised).** 500 changed files per overview (more refuses, it never lists a part),
6,000 diff lines and 1 MiB of line text retained per file (the FIRST lines up to either bound,
a contiguous prefix, never a selection) with the exact totals and the whole diff's digest kept, 1,200 characters per line with the clipped count shown, 4 MiB of diff read per
file, 64 KiB for a new file (a read costs that and the one byte that shows a file is over it,
65,537 bytes), 16 MiB of new-file reads per overview, counted as the bytes actually read, 16 MiB of
tracked names listed per read, 16 MiB of the index's record of the private locations' entries,
2,048 private locations (262,144 bytes of them) left out per read
(more refuses), 64 MiB of index copied per read
(more refuses with its exact size), 1 MiB for each of the repository's `info/exclude` and
`info/attributes`, 20 seconds per git read, one review read at a time. Patch text is untrusted: it is shown as text only, and a character that
could reorder or hide text is shown as a visible `U+` placeholder.

**The unit of every character bound is the UTF-16 code unit.** The 1 MiB of line text, the 1,200
characters per line, a path (1,024), a branch name (255), a hunk's section heading (200), a
detail sentence (240) and a listed content-filter driver name (64) are all counted in the unit the office checks them in, JavaScript's
`.length`, which is also the unit the answer's byte budget is built on (JSON escapes one unit to at
most six bytes, so a file at the total bound is at most about 6 MiB on the wire, inside the 8 MiB
the office reads). It is not Python's `len`, which counts code points: a character outside the Basic
Multilingual Plane, an emoji for one, is one code point and **two units**, so a line of 601 emoji is
1,202 units and a bound of 1,200 shows 600 of them. The backend measures and cuts in that unit,
never inside a character (a character that does not fit whole is left out whole; half of one is
not text), and keeps or cuts a line and its end-of-file marker together by the same measure. The
`N more characters not shown` tag after a clipped line counts CHARACTERS as a reader counts them, so
it says `1 more character not shown` for the one emoji left out. A path, a branch name or a detail sentence is cut
the same way, and a filter-driver name the repository gives that is longer than 64 units is listed cut
to its first 64 (it is listed for disclosure only; before this it made the office refuse the whole
overview); a path longer than 1,024 units is refused like any other over-long path. The office
cuts its own refusal text at a character too. The dialog applies neither bound (it shows what it is
sent); the width estimate it keeps for side by side counts units, which can only overestimate.

**Proof labelling.** The tests drive a real catalog owner over a real running Mission and real git
repositories in temporary directories, and the real App in a real browser over a fixture
WebSocket. That is isolated, fixture proof, not acceptance on a live team: the browser proof on a
real agent's workspace is Root's.

**Why the subtree is `integrations/pixel-agents/`.**

- **It cannot be imported by accident.** This repository's Python lives in top-level
  snake_case packages that `tests/test_static.py` and the compile check reason about. A
  hyphenated `pixel-agents` directory under `integrations/` is plainly not a Python package.
- **One build boundary.** The Node.js dependencies and build stay inside one directory.
- **Upstream tracking.** The name matches the upstream project.

## Licensing

Dodging Infinity is Apache-2.0. The vendored office keeps its own licences: Pixel Agents code
is MIT, the character sprites are derived from a CC0 1.0 pack, and the pixel font is SIL
Open Font License 1.1. The full table and each basis are in
[`integrations/pixel-agents/UPSTREAM.md`](../integrations/pixel-agents/UPSTREAM.md#licensing).

## Upstream updates

The subtree tracks upstream `pixel-agents-hq/pixel-agents` at commit
`d1e007a9fdf3003c252d2973abe1999ae59aec33`. The update procedure and the list of files the
DI office changes are in
[`integrations/pixel-agents/UPSTREAM.md`](../integrations/pixel-agents/UPSTREAM.md#updating-from-upstream).
