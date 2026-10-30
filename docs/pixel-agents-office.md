# Pixel Agents DI office

The Pixel Agents DI office is an early, read-only increment: a pixel-art office in a
browser that **shows** what Dodging Infinity reports about Missions and their roles and,
when the catalog is launched with contract version 2, their task chains, report facts and
review preamble. It lives in its own subtree,
[`integrations/pixel-agents/`](../integrations/pixel-agents/), with its own Node.js
dependency and build boundary.

It observes; it does not control, with TWO deliberate, bounded exceptions. Each is opt-in, and
each is a process you start yourself. The first is the **Codex Orchestrator** request panel (see
[Requesting Missions](#requesting-missions-the-codex-orchestrator)), where you ask for work, decide
on an exact proposal yourself, and start it. The second, with `--delivery-adapter-discovery`, is the
office delivery adapter (`didelivery_office.py`). The office relays to it a status read, and a
request for ONE delivery step you confirm (a commit, a push or a pull-request creation). The adapter
admits or refuses that step under the delivery's own authorization. The office holds no delivery
authority of its own, and has no route to merge, release or deploy. You reach it from a Mission:
Mission details shows **Delivery** under Swarm, and for a workflow Mission whose ONE delivery the
catalog links (see "Delivery links on Missions" under [Launch](#launch), which needs the catalog's
`--delivery-store`), it reads that delivery's status when you ask, and offers the one step the
adapter names eligible behind a local confirmation, with the adapter's two notes shown as it wrote
them. No delivery id is typed there, and no other delivery can be chosen. Accepting CI results is not
connected. A Mission Core Mission, or any Mission without such a link, shows why no delivery is read
and offers nothing to confirm. A plain Herdr task is linked when its workspace origin matches one
delivery record's repository. Commit, Push and Open pull request then use the same confirmation as a
workflow Mission. They are not a layout preview, and they are not offered when no delivery is linked.
The catalog's delivery store is not shown to be the one the adapter
reads: if the adapter has no record of a linked delivery, its refusal is shown as it answered. A
separate panel where a delivery id is typed is kept for test harnesses only, with no product entry (owner
decision, 2026-10-29): the office does not draw it, and Delivery under Swarm is the product's delivery view. Everything else here is
read-only. The office does not spawn Herdr (the panel's own bridge asks Herdr, after your explicit
Start), mark Missions complete, create Git authority, deploy, or rewrite durable state.
It is a browser package only: there is no native wrapper, no signing or notarization, and no
controls beyond that panel. Dots (future
and optional, not today's mobile interface) and R1 are separate and are not part of it. Today
the mobile surface is Grok Bot ([docs/grok-bot.md](grok-bot.md)), which hands requests to the
Codex Outer Operator. The office is not the Phase 11 Visual Mission OS of the
[roadmap](roadmap.md#phases-10-and-11), which remains last.

What a rebuilt office reads from the catalog, and what still needs a process on the machine:

- **Code Diff** is offered for an active Mission Core assignment and for an active Herdr task whose binding is bound, including when no team member is live: the diff is the task workspace. The scan Code Diff follows that offer. It is not a layout preview. A review the office does not offer says the office's reason. Reading the diff still needs the catalog backend (`/code_review`) restarted from this tree.
- **Comments** on a Herdr task are that task's recorded review (reviewer, round, verdict, declared findings). Nothing recorded stays unrecorded. They are not a Paid-sharing thread and not a layout preview.
- **Evidence and review** for a Herdr task come from the task's `.herd/state` review when the capture observes the workspace. No review recorded is `not_recorded`. No Mission Core artifact bodies are recorded for a Herdr task; that section says so, and the review section is the recorded review.
- **Delivery** is read when the catalog links one delivery record. A workflow Mission links by the record's Mission binding. A Herdr task links when its workspace origin matches one delivery record's repository. Commit, push and opening a pull request still go through the office delivery adapter when you start it (`--delivery-adapter-discovery`), and still require that adapter's confirmation. The office itself does not commit, push, merge or deploy, and it does not start Herdr.
- **Reception** reads Herdr task failure (`FAILED`, `ERROR`) as a blocker and a completed Herdr task with no approving review as results ready for review. Clarification requests are still not in the catalog. Recover, answering, and accepting CI stay outside this office and say which connection they need.
- **Herdr** in the sidebar and on a Mission opens the documented terminal path (`herdctl` from your checkout). The office does not start Herdr.
- **Connections** are not in this build.
- **Orchestrator status** uses a bound active Herdr task's standing, and a workflow Mission's declared phase, when no team member is confirmed live.

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

In CI (`.github/workflows/pixel-validation.yml`) the webview suite runs as four parallel shards
balanced by measured per-file time (`scripts/pixel-ui-shards.mjs`, timings in
`scripts/pixel-ui-test-durations.json`); files stay serial inside each shard. The required check is
the final `Pixel validation` job, which passes only when `Pixel checks` and all four shards pass.
To run one shard locally: `cd webview-ui && npm test -- $(node ../scripts/pixel-ui-shards.mjs --shard 1 --of 4)`.
When a UI test file grows a lot slower, refresh its seconds in the timings file so the shards stay even.

The bridge's tests are the repository's ordinary Python suites, for example
`tests/test_catalog.py`, `tests/test_catalog_http.py` and `tests/test_catalog_v2.py`
([CONTRIBUTING.md](../CONTRIBUTING.md#running-the-test-suite)).

## Register the sources

The office reads ONE dedicated catalog registry bound to its sources: the Mission Core store,
the WorkflowStore and the DI-native task source. Run from the root of your Dodging Infinity
checkout. Every path must
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
python3 -B dicatalog.py task-source --registry "$EX/registry" --ensure
python3 -B dicatalog.py sources --registry "$EX/registry"
```

- The `task_native` source is herdctl's own repository list, the directory where
  `herdctl init` writes `repos.json` (`~/.config/dodging-infinity`; `--store-dir` names
  another). `task-source --ensure` registers it as `di_herdr_tasks` and changes nothing once
  that store is registered under any id; without `--ensure` it only reports, with the exact
  command when the source is missing. It is a locator: the repositories it names are only read.
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
- **Delivery proposals in Reception (opt-in).** `--delivery-presentations DIR` is version 2 only
  and is never discovered: you name, by absolute path, the Grok Bot state directory (mode 0700)
  whose `grok_bot_requests.json` the catalog may read, read-only. The catalog reads that whole file
  within a byte bound, parses it as JSON, checks its `schema_version`, and then selects ONLY the
  `delivery_presentations` map (at most 16 proposals the Grok route recorded). The file's other maps
  (requests, presentations, approval consumption, workflow maps, recovery seals) are left unexamined
  and are never projected or logged. Each selected proposal, and each delivery record when
  `--delivery-store` is given, is checked in full in its canonical form, and only the allowlisted
  facts are shown: the proposal digest, when it was presented and expires, its mode, the public
  repository URL, the workflow id it names, and a state. Pass `--delivery-store DIR` too, naming the PR
  delivery store, and each proposal also says whether a matching authorization is recorded;
  without it the state is `unknown`. A proposal is tied to a Mission only when it names a workflow
  id that exactly one registered workflow Mission has; otherwise it is listed on its own with the
  reason. It covers the Grok Bot route only: a proposal presented straight from the command line
  (`present-dots`) is not stored anywhere, so it cannot appear. When the flag is not given, Reception
  labels it "not reported" and does not show it as "none".
- **Delivery links on Missions.** With `--delivery-store DIR`, each version 2 Mission also says
  which ONE delivery record, if any, that store links to it. Only a workflow Mission can be linked:
  by the one valid record whose own Mission binding names its workflow id, while every workflow
  source was read and no other Mission holds that id. Two matching records are ambiguous, and none
  is chosen. A Mission Core Mission is never linked: no delivery record names one, so its link is
  "not known", not "none". A plain Herdr task is linked when repository addressing is on and its
  workspace origin matches exactly one delivery record's repository (`record_repository_binding`).
  No readable origin is `unlinked_repository_not_recorded`. Two records, or two Herdr tasks, sharing
  that repository are ambiguous and none is chosen. Without the flag, every workflow Mission and
  every addressable Herdr task says that no store is registered; a Mission Core Mission still says
  its link is not known. A catalog that did not address repositories still reports
  `unlinked_model_not_recorded` for a Herdr task. A link names which delivery's status may be read; it
  authorizes nothing, and the office delivery adapter is not shown to read the same store. Mission
  details reads a linked delivery under Swarm (see the delivery adapter, above). Commit, push and
  opening a pull request use that adapter's existing confirmation.
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

## Reception: what needs you

**One inbox.** The attention entry in the side panel and the reception desk in the world (the lobby desk with its permanent, decorative
receptionist and a "Reception" label) open the same full-size dialog. It is view only: nothing in the inbox itself approves, starts, retries or
delivers anything (a card's "Open the request panel" only opens the panel), and the receptionist is not a worker and has no authority.

**What it lists.** Six categories: *Blockers*; *Clarification*; *Authorization / reauthorization*; *Failed verification*; *Results ready for
review*; *Delivery awaiting a separate decision*. They are drawn from three kinds of source, each saying what it covers. First, the catalog's
source-qualified Mission observations: Mission Core's state, the workflow-store phase, members' reported activity, the DI-progression pause
flag and the canonical review record, with each category's own limits (some categories exist only at contract version 2, and a category a
source does not report is labelled "not reported by this source"). Not all of that is Mission Core, and the catalog itself reports no
clarification requests; the only clarification entries are the request panel's conversations awaiting your answer. Second, the delivery
proposals the Grok Bot route presented (opt-in, below). Third, the Mission request panel's overview (its 10 most recent conversations and
20 newest requests). A source that was not read, or does not report a kind, is labelled as such ("not reported by this office", "not set
up") and is not shown as "none": delivery is "not reported" until you opt in, and the panel is "not set up" until you configure it. Above the
cards, one or two plain lines say what could not be fully checked and what each source reports ("Delivery proposals: 2 waiting · Request
panel: 4 waiting"); the detail behind them is one disclosure.

**Reading a card.** The first layer is *what happened* (one or two source-backed sentences), *why it needs you* (one sentence) and *what you
can do*, in plain words, with identifiers and codes kept out of it (the Mission's own title, and any text a source wrote, are shown as
written). Exact identities, every cause with its details, reason codes, digests and the full route text are one disclosure away
("Identity, causes and the route to act"). Severity is shown in words, and only where the source gave one.

**What you can do is what the buttons do.** When the request panel lists the same decision, the card says to decide it in the request panel
(it records your decision; the inbox approves nothing) and offers **Open the request panel**. Otherwise it names the Operator route and says
why there is no in-office route: no request panel is set up here, it is not available or could not be read just now, it does not list this
decision, or this kind of item has no panel route.

**One count.** The request panel's own card says how many requests need you and how many conversations are waiting for your answer, with every
number naming what it counts (`1 request needs you · 1 conversation waiting for your answer · 1 request in all`); the
inbox lists those items (a decision the panel and Mission Core both report is one card) and the coverage line says the same total.

**Questions.** A conversation whose newest reply asked questions is listed with the questions as quoted, untrusted text; answering happens
in the request panel. In the panel's transcript the declared `mission-questions` block reads as a short list of questions, with the
assistant's exact reply one disclosure away; replies recorded earlier, or that the panel could not read for questions, are shown exactly as
written.

**Hiding.** "Hide in this view" is a preference of this view. It resolves nothing, approves nothing and dismisses nothing anywhere; hidden
entries come back when the page reloads, and "Show N hidden" brings them back sooner.

**Opt-in setup.** Neither source below is on by default; a source that is off is reported as not set up or not reported. Paths are examples; use your own.

```bash
# delivery proposals (contract version 2): you name the Grok Bot state directory (mode 0700) and, optionally, the PR delivery store
python3 -B dicatalog_http.py --registry "$EX/registry" --discover-roots \
  --discovery-file "$EX/runtime-v2/backend-discovery.json" --contract-version 2 \
  --delivery-presentations /abs/grok-bot-state --delivery-store /abs/pr-delivery-store
node dist/di-cli.js --state-dir /abs/office-state \
  --backend-discovery "$EX/runtime-v2/backend-discovery.json" --contract-version 2

# the request panel: start it (see "Requesting Missions" below), then tell the office where its discovery file is
node dist/di-cli.js --state-dir /abs/office-state \
  --backend-discovery "$EX/runtime-v2/backend-discovery.json" --contract-version 2 \
  --mission-panel-discovery /abs/panel-run/panel.json
```

Delivery proposals cover the Grok Bot route only: a proposal presented straight from the command line is not stored anywhere and cannot
appear. The details and limits of each source are in the Launch section above and in "Requesting Missions" below.

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
  a real agent always wins selection and seating. A posted job (a clerk, a receptionist, a gardener) holds its
  post two and a half times as long as an ordinary stop; a roaming job (a courier, a jogger, a tourist) lingers
  six tenths as long at every stop, so some residents are fixtures of a place and others keep moving.
- **A crowded working office shows only some of its residents at a time.** A pool that holds a real desk (a Mission's room,
  the legacy single Office, the Palace) draws at most min(16, ceil(T / 34)) of its residents on duty, where T is that pool's
  resident-eligible tiles as the world builds them: walkable, on the main pedestrian network, not a seat or a seat's walkable
  neighbour, and in that place (not the room's or place's area). The rest are off duty: still residents, with their ids, jobs,
  routines, ride plans and positions exactly as they were, but frozen, not drawn, not hoverable and not in anyone's way. One steps
  off at its next pause and the next steps on, one hand-over per working office every six seconds, so the office stays alive. In the default office as shipped
  (T = 105) that is 4 of 140; in the expanded-office preset (T = 718) it is 16 of 140; the Palace (T = 2183) holds 11 residents,
  below its bound, so this rule draws all of them. (That rule does not make the Palace's interior unchanged: where its residents stand is the
  separate interior rule below, which supersedes the earlier statement that the Palace was already readable.) The legend reads "Ambient residents · 4 on duty of 140 ·
  decorative". Nothing is deleted or re-identified, other districts and the subway riders are not rotated, nothing off duty is
  saved as such (it is derived from the world and the clock), and a snapshot saved at the older density loads exactly and shows
  the on-duty count when the office reopens. The values were chosen from built screenshots at normal zoom; the six-second cadence
  from moving frames only.
- **The reception post (DI office).** The lobby desk has one permanent, stationary receptionist (decorative: not an agent, not on a seat, without the working
  glow and not selectable) in a fixed headset and name badge drawn from code, with its own "Reception" label. Its own
  workstation, the one chair that faces the desk, is reserved: no seating path of this build assigns it, and a saved seat id for it
  is ignored. The tile it stands on is blocked for agents, pets and the residents' routes, and the residents' plans do not target it; a
  resident whose route would cross it is detoured around it, within a bounded search. **One honest exception:** when that tile is the ONLY way through (blocking
  it would cut the lobby in two), it is left passable, and residents and agents walk through it and briefly overlap the decorative
  receptionist. That is a documented fallback for such a layout, not a missing check; on the shipped worlds the tile is blocked. The
  Palace has no second, ambient receptionist: its three former receptionist slots are posted gardeners tending the lobby plants (a posted role,
  like the one replaced, so the lobby keeps three residents who stay), in place, so the number of residents is unchanged; the Atrium and the
  Observatory keep theirs in their lobbies.
- **Real agents across the three grand offices.** In a large world with an explicit home, the offices are found from the layout itself:
  the home (the Palace) and every office district (Botanical Atrium, Deco Observatory) that holds at least 12 desk-facing chairs and can
  be walked to from the home. A *workstation* is a reachable chair that faces a desk **and** faces a PC or monitor, by the same rule that
  switches a monitor on when an agent sits down (so the lit screen and the seating agree). A reachable chair at a desk-category piece with
  nothing electronic in front of it (a long library or meeting table, a coffee table) is a *table seat*, labelled "table, not workstation";
  every other chair of the Palace (its lounge chairs) is labelled "chair, not workstation". All three kinds remain eligible seats: an
  agent that holds one now, or that this build remembers for its identity, keeps it. Seats an earlier build saved by a per-run character number
  are not trusted: those agents are placed once by this rule (the one-time migration below) and then remembered by identity. A new agent is placed on a workstation while one is free in any office; only
  when none is free anywhere is a table seat used, and only when no table seat is free either is a chair used, each labelled as what it
  is. On the shipped living metropolis (after the receptionist's reserved bench) the measured seats are: the Palace 124 eligible = 44
  workstations (40 at desks, 4 at monitored long tables) + 21 table seats + 59 lounge chairs; the Botanical Atrium 90 = 72 workstations +
  18 table seats; the Deco Observatory 90 = 72 workstations + 18 table seats; 188 workstations in all. The rule for a FRESH placement of N
  agents over E offices is a target of floor(N/E) workstations each, the remainder going to the first offices (Palace, Atrium,
  Observatory); the targets give way to valid existing preferences (an agent
  that already has a valid seat keeps it, which can leave an office above or below its share) and to capacity (a full office's share moves on
  to the next). A seat an agent holds now or that is remembered for its identity counts toward its office's share whatever kind of seat it is (a table seat or a
  lounge chair included), so a roster already seated in one office leaves that office fewer new agents. An office that is over its share
  still receives a new agent when it is the only office with a free workstation: nobody is left without a desk while any workstation is free.
  The *at least 12 desk-facing chairs* that make a district an office count workstations and table seats together, not workstations alone,
  so which districts are offices does not depend on whether a desk has a monitor. Agents are taken team by team (the lead first) and a team stays together while its office has room; a lone four-agent
  Mission placed fresh is 2/1/1. An agent that already sits somewhere stays there while its seat, the layout and its identity remain
  valid; a seat that disappears, becomes reserved or falls outside the detected offices is re-placed by the same rule. Worlds where offices
  are not detected keep their existing (fallback) seating behaviour. Where each agent likes to sit is kept by its canonical identity
  (`seat-preferences.json` in the state directory: at most 512 entries and 128 KiB; identities that are present now are protected, the
  history of absent identities is evicted first, oldest first, and an active set over the budget is a reported, deterministic subset; every
  identity component is bounded by the catalog contract's own text bound), and not by the server's process-local number, which names a
  different agent after a restart: the number-keyed seats an earlier process saved are not offered to a new one. An existing single-office
  roster is therefore re-placed once, by this rule, and is then stable under the same conditions as any other seat. Placement does not edit
  saved layouts.
- **Calmer office interiors, still alive.** The residents of the three offices stop in the lobby, corridor and courtyard rooms (the rooms
  with no desk-facing seat at all: neither a workstation nor a table seat, so a library or meeting-table room counts as a work room for them),
  not at the desks: none of their posts or stops is in a work room, though they can still walk through one on the way
  between rooms. Their one long walk is shared out: of an office's
  roaming residents (couriers, custodians, visitors), taken in roster order, only every fourth makes the single cross-world visit to another
  place, and the others keep to the office's own rooms. (Every roaming resident used to make that visit, and the walk took most of its time:
  measured on this world, a roamer spent 66 to 88 percent of its time outside its own office rooms.) The Atrium and the Observatory are working
  offices now, so their residents rotate on and off duty like the Palace's. The residents of every other place keep their plans in every shipped preset, and in the living metropolis their looks and start
  timers as well (tests pin all of it against the previous build's code path); an office resident that differs from the previous build is
  reset fresh by the migration below.

  **What is compared.** The comparator is *not* an execution of the previous build. It is the previous build's resident plans (the "S4
  ambient plans"), **reproduced on this final candidate engine with the office switched off**. With 24 agents the idle agents wander at
  random and the comparator's residents get caught up among them in the work rooms, so the 24-agent comparator is the **mean over the fixed
  seeds 1 to 12** (`Math.random` seeded per run); with no agents it is one deterministic run. In these runs the candidate's counts were
  identical with 0 and with 24 agents (its residents may still walk through work rooms; none was found standing in one at the sampled
  instants).

  Measured from the residents' actual tile positions (a resident of the office standing on a tile of one of its rooms): **121 samples, one
  every 10 *simulated* seconds, over 1200 s**, with no agents and with 24 agents (8 per office, seated at every third workstation of each
  office's detection-order list, so the Palace's agents sit at its workstations, not at table seats). Figures are residents of the office
  on average per sample, from the final source state's run of `ambientOfficePresence.test.ts` (deterministic: seeded):

  | Office | Comparator: anywhere in the office, 0 agents | Comparator: anywhere in the office, 24 agents (mean of seeds 1 to 12; range) | Candidate: in its non-work rooms, 0 / 24 agents | Standing in work rooms: comparator with 0 agents, average (peak); candidate | Fewest in the candidate's non-work rooms at any of the 121 samples |
  |---|---|---|---|---|---|
  | Palace | 2.71 | 4.72 (3.43 to 6.07) | 5.35 / 5.35 | 0.70 (7); 0 | 1 |
  | Botanical Atrium | 3.72 | 3.82 (3.76 to 3.84) | 5.15 / 5.15 | 0.64 (5); 0 | 2 |
  | Deco Observatory | 3.66 | 3.67 (3.64 to 3.72) | 4.45 / 4.45 | 0.58 (6); 0 | 1 |

  The candidate's non-work average is at least the comparator's whole-office average in every office, with and without agents, and no
  resident of an office was found standing in a work room at any of the 121 samples. At the sampled instants t = 0 / 600 / 1200 s the
  Palace's non-work rooms hold 11 / 1 / 5 of its residents, the Atrium's 8 / 4 / 5 and the Observatory's 8 / 6 / 4. Those are statements about
  the sampled instants, not a promise for every moment, and whether that reads as "calm but alive" is for the eye (see the time-lapse crops
  in the README).

  *History, labelled.* An earlier measurement of the same comparator gave the Palace's 24-agent mean as 5.15 (range 4.10 to 6.03) and the
  satellites' as 3.83 and 3.66. It seated the 24 agents at every desk-facing seat of the list; this table seats them at the workstations
  only, which moves all eight of the Palace's agents and none of the satellites' (a rerun of the same code with each list reproduced both
  sets of figures). The 24-agent comparator moves with where its agents sit, which is why the seat list is stated; the candidate's figures,
  and the comparator with no agents, did not change.
- **Saved residents survive that change.** A snapshot saved by the previous build is recognised only if it is exactly that build's plan
  for this same layout (the exact predecessor, rebuilt from the same code): residents whose plan is unchanged are restored exactly, the few
  that changed start fresh at their posts, and the result says so ("migrated", with the counts) rather than claiming an exact restore. On
  the living metropolis that is 118 restored exactly, 27 starting fresh (24 whose routine changed and 3 new) and 3 retired, with the two
  trains and their riders carried over; a world whose plan did not change (the expanded office) applies exactly, with no migration. If a resident who was waiting for or riding a
  train is among the changed, the whole migration is refused (`migration_unsupported:in_transit_resident_changed`), nothing is applied,
  and nothing is saved over that file for the session. Anything else that does not match is another world, as before. Nothing is saved
  before the restore attempt has been recorded, so the saved file is not replaced before the restore attempt has been recorded.
- **Limits of the receptionist, the interiors and the migration, said plainly.** The receptionist's tile adds no waiting of its own: in a
  one-tile-wide test corridor crowded with wandering agents the replans and the longest wait are identical with and without it (the long wait,
  151 s in that synthetic corridor, is the existing give-way behaviour among real agents, not caused by the post), and on the living
  metropolis no resident was found stuck at or because of the post. That is not a claim that nobody is ever stuck anywhere. The interior
  figures above are measurements of the sampled instants on this world, against the previous build's resident plans reproduced on this engine (the
  24-agent comparator a seeded mean, because its idle agents wander at random); they are not a guarantee for other layouts or other agent counts. The migration recognises the previous
  build's plan only: a snapshot from any other build, or for a different resident set, is treated as another world and is not applied.
- **Quick views.** With the offices detected, the camera controls gain an Atrium and an Observatory button (the same jump as the
  Districts list).
- **Subway.** Four stations and two trains. A bounded set of residents (24 in this world) ride them: each walks
  from its post to a station's island platform, waits there (its label then says "waiting for a train"), boards the
  train that is heading its way, rides, and alights at the station its journey names, on foot and by train, never by
  camera. A journey is one of three kinds, chosen once per resident from the seed: a *commute* (to a station at least
  two along the line, passing one), a *hop* (to the next station) or an *excursion* (round three stations and home).
  Riders are residents of the stations and of the Grand and Market Boulevards and of open-ground places within 96
  tiles of a platform; the platform tiles are dealt out, so riders waiting for a train never share a tile. The
  Stations list moves the camera only: a jump is instant camera travel, not a ride.
- **Saving and moving the simulation.** While open, the office saves the residents' and trains'
  state (identities, routines, journeys in progress) under its own state folder. Settings,
  Export Ambient Snapshot downloads it; Import Ambient Snapshot (paste JSON) restores it. A
  snapshot from a different world, resident set or journey plan, or over 65,536 bytes, is refused whole and
  nothing changes. (The snapshot is bound to each resident's routine and ride plan, so one saved before the
  journey plans were extended is ignored once, and the residents start from their seed.)
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
  A plain Herdr task uses that same preamble when the capture observes its workspace: the
  review is bound to the task record's own id. No review recorded is `not_recorded`. A
  workspace the capture did not observe is `unavailable`, with that reason. Herdr tasks
  still have no Mission Core artifact library; those sections say they are not reported,
  and artifact bodies are not read for them.
- **What it writes.** Registration writes only its registry. The adapter writes only its own
  discovery file. Neither ever writes a store.
- **Routes.** `GET /catalog`, `GET /diagnostics`, `GET /healthz`, `GET /artifact` and
  `GET /code_review`, and nothing else. `GET /artifact` returns one recorded artifact's text for
  the evidence library. It takes an admitted reference (Mission, artifact id, listing cursor),
  never a path.   `GET /code_review` returns the read-only view of an active assignment's workspace
  (a Mission Core record or a Herdr task whose binding is bound),
  or of a COMPLETED Mission Core Mission's recorded workspace when its execution is established and the
  workspace names the run (see [Live code review](#live-code-review)); it takes a Mission and a task id, and for one
  file the snapshot and file id the overview listed. Never a path, a ref or a git option. All
  routes are loopback-only (`127.0.0.1`), behind one bearer token compared in constant time,
  with exact `Host` and `Origin` checks. There is no CORS header, no cookie and no 5xx.
- **No route to action.** It imports, proxies and invokes none of the consequential entry
  points (`direquest.py`, `dirun.py`, `grokbot.py`, `codexgw.py`, Git delivery).

**The office only reads the bridge.** (Two separate, opt-in connections are not the bridge. The
request panel, described in [Requesting Missions](#requesting-missions-the-codex-orchestrator), is
relayed one closed message type, `diMissionRequest`. The office delivery adapter is relayed one
closed message type, `diDeliveryRequest`, with two operations: a status read and one confirmed
step. Neither token is ever held in the browser, and the catalog bridge below stays read-only.)

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

**Who is offered it.** Three cases, asked by two different references. (1) A live member of the
DIRECT task of a Mission Core Mission whose standing is `active` and whose binding is `bound`:
the review is opened from the agent. (2) The same, for a plain Herdr task (`herdr.task/1`) whose
standing is `active` and whose binding is `bound`: the workspace is that task's bound
`workspace_realpath`, and `/code_review` is asked with the Herdr mission ref
(`<source>:<task id>@<root key>`). An unbound Herdr task is not offered, and the page says the
workspace is not confirmed. (3) A **COMPLETED** Mission Core Mission, as a whole, in the Mission's
Code Diff: offered only when the office lists the Mission's historical recorded execution as
**established** in a current listing, and read only when the backend also finds that execution
unique and unchanged and the recorded workspace's own task record names this run. It is asked
for by a Mission-level reference that names no agent. A finished Herdr task is not that historical
Mission Core execution, so it is not offered as a finished Mission. A child-task member does not
work in the Mission's workspace, a v1 office corroborates no workspace, and a BLOCKED, CANCELLED
or CLOSED Mission is not offered a review. A finished Mission Core Mission is read **as the
workspace is now**, with ownership checked at one instant (and again at the end of the read): a
root since reused for other work is refused, not shown. Where a review is not offered or is
refused, the page says why in words. The target comes from the catalog record: the renderer never
supplies a root, a ref, a pathspec or a git option, and similar names or paths are not identity.

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

## Requesting Missions: the Codex Orchestrator

The one place the office does more than observe. **Codex Orchestrator** is a panel in the
office where you ask for engineering work in ordinary words, read an exact proposal, decide on
it yourself, press **Start**, and then watch the team's work progress and a verified result
appear. It is built from existing parts and adds no second engine: the conversation is the
existing Codex capability in a restricted, read-only posture; the proposal, its revisions and
its digest are Mission Core's; the decision goes through the local request surface; the work is
started and observed by the existing Mission bridge; and the engineering is Herdr's.

What it does **not** change: it carries no delivery authority. It never commits, pushes, opens
a pull request, merges, tags, releases or deploys, and a verified result is not a delivered one.

### What you run

Three processes, all on the one machine, all started by you, and none by the office:

```bash
# 1. The request panel (Python). It holds your proposals, decisions and the conversation.
python3 -B dimission_panel.py \
  --state-dir /abs/panel-state --control-repo /abs/control-repo \
  --discovery-file /abs/panel-run/panel.json \
  --workspace-repository /abs/the-repo-the-work-is-about --workspaces-root /abs/mission-workspaces

# 2. The progression runtime (Python). It moves a STARTED Mission along. Same four locations.
python3 -B dirun.py \
  --mission-state-dir /abs/panel-state --mission-control-repo /abs/control-repo \
  --mission-workspace-repository /abs/the-repo-the-work-is-about \
  --mission-workspaces-root /abs/mission-workspaces run

# 3. The office (Node), told where the panel's discovery file is. From integrations/pixel-agents/
node dist/di-cli.js --state-dir /abs/office-state --backend-discovery "$DISCOVERY" \
  --mission-panel-discovery /abs/panel-run/panel.json
```

- **Absolute paths, owned directories.** The panel and the runtime refuse relative paths. The
  panel publishes its discovery file (`{url, port, token}`) 0600 in a 0700 directory; only the
  office's server reads it. **The token never reaches the browser**: the page holds no token, no
  shell, no terminal, no filesystem access and no command route. It can only send eleven closed
  message operations, each with exactly its own fields, which the server relays to the panel on
  `127.0.0.1`.
- **The same four locations for the panel and the runtime.** `--state-dir`, `--control-repo`,
  `--workspace-repository` and `--workspaces-root` of the panel are `--mission-state-dir`,
  `--mission-control-repo`, `--mission-workspace-repository` and `--mission-workspaces-root` of
  the runtime. The runtime publishes a status the panel compares against its own locations, so a
  runtime started for other locations is reported as such rather than as yours.
- **Without the workspace pair** the panel can converse, propose and record a decision, but it
  cannot start work (and says so).
- **Without `--mission-panel-discovery`** the office shows the panel as not set up and says what to
  start. It never starts the panel.
- **`dirun.py` has two modes.** The four `--mission-*` options select MissionCore mode before any
  workflow machinery is built, and never combine with `--config` or `--runtime-config`. Without
  them it is the existing workflow Runtime, unchanged. The two modes use different lock files and
  share no state.

### The workspace

Ask and Requests are two tabs of one workspace that is drawn in the side panel's own palette, type scale, lines and controls, so the two read as one product.

- **Header and tabs.** The title and the compact **Wider reading** and **Close** controls share a row; the one-line tagline runs under them. The Requests tab carries a small chip that says what it counts ("1 request needs you"); its text is its accessible name, so it never relies on colour.
- **One persistent context area** sits under the tabs, never hidden and with no way to dismiss it. In a fixture office its first row says the conversation, proposals and results are synthetic and that no approval given there is real (the full original wording is the disclosure "The full notice"). Its second row says whether automatic progress is on or off and what that means after Start, with the existing "Details about automatic progress".
- **Ask.** Each turn is labelled **You** or **Assistant**. One note above the transcript says the assistant only drafts with you, that its words are not a decision and carry no authority, and that decisions happen in Requests; each assistant turn also carries that meaning for assistive technology. A reply with questions the panel could validate shows them as a numbered list with the exact reply one disclosure away. An **older** reply that was recorded without question reading and contains a closed technical block shows its prose, the note "This earlier reply includes a technical block (view only; not a current question)." and its exact original behind "The exact original reply". Such a reply is never listed as a question, never counted and never changes any state; an unclosed or malformed block, or a reply that would be left empty, is shown as written.
- **Requests.** The open request leads with its **plain title** (the first line of what you asked for), then its **status and next step** in the panel's own words, then one closed disclosure, **Exact references (technical)**: the request id, the Mission, the revision, the digest (the whole digest one control away), "Read from the record …" and the sentence that Mission Core holds this revision with that digest. The proposal below is shown in full; where a permitted action or a rule has a raw key, the plain words come first and the exact key follows, muted, never dropped. The decision section opens with exactly what approving binds to: "You are approving revision N of this proposal (digest …)".
- **Counts.** The sidebar card and the tab chip say what they count in words: requests that need you, conversations waiting for your answer, requests in all. They come from the same facts the Reception inbox uses.

**Sizes.** The workspace sits beside the side panel when there is room and otherwise covers the window and says so in words. The rule (`layoutFor` in `diMissionRequestModel.ts`): the side panel is 420 px; the workspace keeps at least 240 px of the world visible; it takes 600 px (960 px in wider reading) when there is room and covers the window when fewer than 360 px would remain. With the side panel shown that is a covering workspace below a 1020 px window; with the side panel hidden, below 600 px. Wider reading puts the request list beside the open request only where the pane is at least 640 px wide (`MR_SPLIT_MIN_PX`); a narrower pane stacks the list above the request exactly as ordinary reading does, so wider reading is never worse than ordinary reading at the same width. Escape closes the workspace and returns focus to the control that opened it; if the side panel was hidden while the workspace was open, focus goes to the control that shows the side panel again. Tab visits the workspace's own controls in order and then leaves it (it never traps focus). Where the workspace covers the window it sits above the office's own overlays (the ambient legend, the agents' labels, the world and city controls) so none of them letters over its text, and below the greeter's bubble and every dialog, which still open over it; beside the side panel it covers none of them. That order is pinned by `webview-ui/test/diMissionRequestStacking.test.ts`.

### Requesting

Open **Ask the Codex Orchestrator** in the office panel and describe the work in your own
words. Codex may ask questions first. When the request is clear it drafts a proposal.

- **A draft is not a request.** It is text in a conversation. Nothing is of record, approved or
  started until you submit it, and the panel says so on the draft.
- **What Codex can do here:** read, ask and draft. Each turn is a fresh read-only Codex
  invocation that sees the earlier turns quoted as untrusted history, with a 300-second
  deadline. It cannot approve, start, run commands or change files, and nothing it writes can
  reach the decision. A reply that says "approved" changes nothing.
- **What Codex writes into a proposal:** four fields only (the objective, where it applies, what
  is in and out of scope, and the action scope). The panel itself sets the repository, no delivery
  target, and the completion requirement (the team's result and its review are bound to this run
  and can be recovered). Control characters and markup in anything Codex or the team wrote are
  shown as visible, inert text.
- **A turn whose outcome is unknown** (the provider timed out or the process died) is shown as
  UNCERTAIN and is never retried for you.
- **A long conversation is shown as its most recent turns, and the window says so.** The panel
  sends at most 50 turns and about 3 MB of text at a time, measured in the bytes that really cross
  to the window (not in characters: the same 8,000 characters are 8 KB of plain text but 32 KB of
  emoji). When older turns are left out the Ask tab says how many are shown of how many, that the
  rest are kept in the record, and which bound decided: for example "Showing the most recent 48 of
  50 turns. 2 earlier turns are kept in the record and not shown here: the turns shown already
  reach the about 3 MB of text this window receives at once." A bound that held for plain text and
  failed for real text would be worse than none.

Press **Submit as a proposal of record**. Mission Core then holds the proposal with a revision
and a digest, and the panel opens it.

### Reviewing

The proposal of record is shown exactly as held: what you asked for, where it applies, what is in
scope and what is not, what the team may do (for example "change files in an isolated copy of the
repository"), the repository, delivery (none), **the proof rules** (what counts as done), and its
identity (request, Mission, revision, digest). Read all of it before deciding. Long text opens in
place; you can choose wider reading.

**The proof rules are shown in full, in sentences, and again inside the offer you approve.** Every
rule the proposal binds appears with its exact numbers, keys and digests: the proof the team must
provide (with the kind of evidence, the artifacts it needs and how old evidence may be), the
artifacts that must match a content digest, the prerequisites that must be met first, the
resources that must be ready, the blockers that may remain, and how many attempts and checkpoints
the team may use. The rules are part of the proposal, so a revision that changes **only** the
rules is a different proposal with a different digest: a window that showed the old rules is
refused as out of date and must read the new ones before anything can be approved.

**Approval is refused for rules this window cannot show.** If the proposal carries anything the
window does not know how to display (a rule this version has never met, or a list longer than the
canonical bound), the window says exactly that, shows no Approve control, and sends no decision.
Showing less than the contract and accepting anyway is the one outcome that must not exist.
Withdrawing stays possible. A proposal that states no proof rules at all (an older record) is
shown as exactly that.

Before you can decide you are shown **the exact offer**: the same identity, the permitted scope,
the repository, delivery targets, an **absolute expiry** (fifteen minutes from when the offer was
first shown) and an **offer digest** naming all of it.

- **The offer is frozen.** While it is live every window is shown the same offer with the same
  expiry. Reading it again does not extend it. Time you spend reading inside the window is fine;
  after the expiry it is gone.
- **Two windows never make two approvals.** A window that holds an older display finds the
  proposal changed and must read the current one.
- **Two different refusals, never confused.** "The offer you were shown is no longer the offer of
  record" means the proposal changed, was withdrawn or replaced, or the offer was not the one
  issued. "The approval window you were shown has passed" means only time passed: nothing about
  the proposal changed, and you can read a new offer.

### When a Start is in doubt

If a child's startup fails after its roles were contracted, Herdr leaves a
bounded, private diagnostic in the child workspace, and the panel shows **why**
under the stage when it can bind that file to this exact attempt: the stage it
stopped at, the failure's type and each role that could not be bound (for
example "lead1: the recorded binding carries no session"). It says which file
holds it (the file's name only; this window cannot open it). When none binds, or
more than one claims the attempt, it says the cause is **not known** and never
shows the newest file as a guess. The Start stays what it was: durably recorded,
in doubt, never replayed, and the panel does not say a retry is safe. See
`docs/operations.md`, "When the roles start but do not bind".

### Approving

Type the word **`approve`** (case and surrounding spaces are ignored; anything else is not a
decision) and press **Approve**. The control stays unavailable until the offer is displayed and
the word is typed, and it says why.

**Approval is Operator-attested. It is not verified human authorship.** Read this line on
screen before you decide, because it is the honest ceiling of this feature:

- The panel relays the word typed in this window. Dodging Infinity has no authenticated human
  principal here, so it **cannot confirm who typed it**. A mistaken or malicious process running
  as your account could fabricate it. The record says so: `operator_attested_not_independently_verified`.
  Nothing here is a verified human signature.
- Nothing else can approve. The ordinary unauthenticated "approve" of the local request surface
  stays refused. Text from Codex or from the team can never be a decision, and a decision made in
  another conversation is not carried over to this one.
- The approval binds exactly the offer you were shown: its Mission, revision, digest, scope,
  repository, delivery and expiry. It is checked against both the offer the panel retained and
  Mission Core's current record before anything is written.

**Approving starts nothing.** Starting is a separate, explicit act.

### Declining, rejecting, or asking for changes

Beside **Approve**, on the same offer, you can decide not to approve (owner decision (c)). Each is
recorded in Mission Core exactly the way an approval is, as an Operator attestation of your choice
that is **not independently verified**, and each binds the Mission, revision and digest you were
shown, checked against the retained offer and Mission Core's current record before anything is
written. None of them starts anything or grants any authority, and none of them is a withdrawal.

- **Decline** records that you did not approve this proposal. It is Mission Core's existing
  DENY: not final, and a new proposal can be submitted.
- **Reject for good…** asks once more (**Confirm: reject for good**), because a rejection is
  final: the proposal becomes REJECTED and can never be approved, revised or started.
- **Ask for changes** needs your reason (at most 2000 characters, not blank), which is recorded
  exactly as you wrote it and shown back on the request. The proposal is CHANGES_REQUESTED: it
  can no longer be approved as it stands. Submit the revised proposal as a new request; it goes
  through the same approval flow. Asking for changes submits and approves nothing.

Once you have declined, rejected or asked for changes, the proposal can no longer be withdrawn:
withdrawal stays what it was, the requester taking back a proposal that has no decision yet. If
the panel cannot tell whether your choice was applied, the request says **Outcome unknown**; it
is not applied again, and choosing the same outcome on the same proposal only reconciles it from
Mission Core's own record.

### Starting, and what happens next

Press **Start** (before the approval expires; an expired approval is shown as expired and is
never quietly re-offered). Start records DI's intent to start the team **before** asking Herdr
for it, prepares an isolated copy of the repository for this Mission, and asks Herdr to start the
team.

- **Start is durable intent, not an exactly-once guarantee.** If the outcome is unknown (the
  request may or may not have reached Herdr) the panel shows **Start outcome unknown**. The work
  is **never started a second time**; the unknown start is reconciled from Herdr's own records.

**Progression is automatic after an explicit Start.** The progression runtime (`dirun.py` in
MissionCore mode) passes about every five seconds over the requests this panel submitted that you
approved and have not withdrawn. On each pass, for each one, it does at most one step, by the
Mission's own record:

- no Start recorded: **nothing**. The runtime never starts a Mission and never acts on an
  approved-but-unstarted one;
- Start recorded, no team identity yet: one bounded, evidence-only reconcile (at most one every
  30 seconds, 16 attempts, then a durable stop that is never reported as success);
- team identity known: observe it;
- running: one fresh read, and a verification is recorded **only if every condition holds in that
  same read** (the team stopped, it succeeded, the result and review files are the ones reported,
  the baseline did not move, the protected surface is unchanged, the approved completion
  requirement is met);
- otherwise it waits. A team that **stopped without succeeding** is a **hold**: nothing is
  recorded, it is re-read every pass, and ending the run is your explicit decision. The runtime
  never turns waiting or a timeout into success, and never ends a run on its own.

You can watch the progress in the office and in the panel. The panel keeps no page-polling
duty for it: closing the panel does not pause the work, and reading it does not advance it.

The line at the top of the panel says whether this is happening. Each state has its own word, and
the word is never softened:

| It says | Meaning | What you can do |
|---|---|---|
| **Automatic progress is ON** | A runtime for these locations is passing. | Press Start, then wait. |
| **OFF** | No runtime has reported here. | Start the runtime, or advance the request by hand with the buttons it then offers. |
| **STALLED** | A runtime reported before but has stopped passing. | Look at the runtime process. |
| **NOT FOR THESE REQUESTS** | A runtime is running for different locations. | Start one with this panel's four locations. |
| **UNKNOWN** | Its status cannot be read. | Treat nothing as advancing. |
| **has FAILED** | It is running but its last pass failed as a whole. | Look at the runtime's stderr. |
| **UNAVAILABLE** | This window cannot read the panel now. | Nothing shown is current. |

Use **Details about automatic progress** (a keyboard-operable disclosure) for the pass count, the
age of the last pass and the runtime's own status words. While the runtime is ON the manual
Observe, Reconcile and Verify buttons are not the ordinary flow; when it is not, they are offered
and the screen says nothing is advancing automatically.

### Withdrawing, revising, and the words on screen

- **Withdrawn.** Taking back a pending proposal is a **withdrawal**, permanently closed. It is
  not a decision and is never called "denied": no one with authenticated authority is involved.
- **Revised.** To change a proposal you describe the change; the pending one is withdrawn and a
  **separate, new** proposal replaces it (a new Mission at revision 1). Nothing is edited in
  place, and the old one reads "Revised". The history stays.
- **The withdrawal key is held in the panel's memory only.** It is shown once and is lost when
  the panel restarts; a proposal made before a restart can then still be read and decided, but
  not withdrawn from this panel.
- **Expired** means the approval's time passed before Start.
- **Uncertain** is shown as uncertain: an unknown conversation turn, an unknown submission, an
  unknown Start. None is retried automatically, and none is shown as success.

### The result, and the observation and delivery boundary

When the team has stopped successfully and DI's single fresh read holds every condition, the
request reads **Verified result** and you can load it: the team's result text and the team's
review, each bounded for display, with the digest each is bound to. If the files are gone or
changed, the panel says the result **cannot be recovered**; a digest alone is never shown as a
result.

- **What "verified" means.** DI decided it from a fresh read of the team's own files after the
  team stopped, against the exact digests it recorded. The team's review is **evidence the team
  produced, not independent verification.** "Engineering complete" (the team says COMPLETE),
  "verified" (DI's conjunction held) and "delivered" are three different things.
- **What the panel observes.** Herdr's own records, read-only: the task record, the control-side
  spawn records, the Mission's isolated workspace files. A recorded observation is history, not a
  claim about this moment. The office shows the team live.
- **What is never delivered from here.** Nothing is committed, pushed, merged, tagged,
  released or deployed by the panel or the runtime: `delivery_authority` is `none`, the request
  carries no delivery field, and a verified result says "it has not been pushed, merged or
  released". Delivery stays a separate, human-controlled step behind the Git approval gates in
  [operations](operations.md).

### Limits, said plainly

- **No authenticated approver.** See above: approval is an operator attestation.
- **No exactly-once.** Durable intent is recorded before an effect, and an unknown effect is
  reconciled, never repeated.
- **The conversation is not the record.** What Codex said is untrusted history; only the proposal
  of record, bound by digest, counts.
- **Bounded.** Conversation text, turns kept, requests listed and offers kept are bounded, and a
  full store refuses a new one rather than evicting an old one. A runtime pass examines at most
  32 Missions and rotates through the rest across passes, and says when it was bounded.
- **One runtime per state directory.** A second one is refused. A Start and a runtime pass never
  act on the same Mission at once (a per-Mission guard shared by both).
- **Only this panel's requests.** The runtime advances only what this panel's journal submitted.
  Another surface's request, a Mission proposed some other way and a workflow record are not read.
- **Reconnect re-reads.** A reloaded or second window re-reads the records; it never trusts its
  last screen.

### How this is tested, and what that does and does not show

The panel, the runtime and the office relay have backend, relay and model tests. The browser test
`webview-ui/test/diMissionRequest.test.ts` loads the actual App (`test/harness/di-app.html` →
`src/main.tsx`) mounted **from source by Vite's dev server** in a real Chrome, through the real
server relay, the real Python panel and the real `dirun` MissionCore loop. It is **real-App
integration over source**, with these controlled and labelled substitutions: the Codex process's
replies, the process Herdr would spawn, and the team's observed state and files. It shows, among
other things, a Mission advancing to a retrievable verified result **while the page is held
unable to read anything** (the adapter's call counter does not move), focus kept across every
transition, and the honest wording. It is **not production-package browser proof**: the production
package is a separate artifact opened separately, and no live provider or live Herdr is involved.
`DI_REVIEW_SHOTS=<dir>` also saves labelled fixture screenshots and a manifest of the source they
came from.

### Verifying a package against its source

`integrations/pixel-agents/scripts/di-candidate-manifest.mjs` links frozen source to a built
artifact without trusting how the build was done:

```bash
node scripts/di-candidate-manifest.mjs digest                      # the name of the exact source bytes
node scripts/di-candidate-manifest.mjs write --out M.json --artifact ../pixel-agents-di-package.tgz
node scripts/di-candidate-manifest.mjs verify --manifest M.json --artifact ../pixel-agents-di-package.tgz
```

The digest is sha256 over every file of the checkout (path and bytes; a symlink by its target,
never followed), except that every entry whose **name** is on the manifest's `excluded` list is
skipped wherever it appears and **whatever it is**: a file, a directory or a symlink. The list is
version-control metadata (including a worktree's `.git`, which is a *file* holding an absolute
path), dependencies (including a symlinked `node_modules`), build output, caches, the local
evidence store and `.DS_Store`. Excluding by name is what keeps the digest **portable**: the same
source gives the same digest wherever the checkout lives, and a clean archive of the base with the
changed paths copied onto it has the same digest as the working tree (a test checks exactly that).
An artifact that is a directory is hashed whole, with nothing excluded. `verify` exits non-zero
if the checkout is no longer the frozen source or an artifact is not the one recorded. It is a
name for bytes and a tamper check, not a signature.

## The living city: lighting, seasons, weather, traffic, residents and the subway

The City button in the bottom toolbar opens a panel with four choices: lighting, seasons, weather and traffic (traffic is described in "Moving traffic", below). The panel never grows taller than the window: its heading and a Close button stay put and the choices scroll inside it; Escape closes it, from the City button that opened it as well as from inside it, and focus goes back to the button. The residents and the subway (described above, under the living metropolis) are a fifth part of the same city, beside the four choices, and have no switch of their own. All are **decoration**: they are
drawn on your screen and change nothing about Missions, agents, their activity, an assignment, an approval or a
delivery. No weather service, location or credential is used, and the office never reads or sends anything to
make them work.

**Lighting** has four choices:

- **Day** is the city exactly as it always looked. It draws no extra layer and costs nothing. It is the default.
- **Dusk** and **Night** are still stages you choose. Nothing about them advances.
- **Clock** follows *this computer's* local time of day while the view is open (night until 05:00, a dawn ramp to
  07:00, day until 17:00, a dusk ramp to 21:00, night after). It is a function of the clock, **not a simulation**:
  nothing is simulated while the view is closed, asleep or rebooted, and opening the office later simply shows
  whatever stage the clock is at.

At dusk and night the outdoors darkens, but offices stay lit, real agents carry a soft light, and street lamps and
lanterns glow. Labels, bubbles, the selection and every panel are drawn after the lighting and are never darkened.

**Seasons** are **Automatic** (the default), **Spring**, **Summer**, **Autumn** or **Winter**, with **Pause** and **Resume** for the automatic turn.
Summer is the city exactly as it always looked, so a new city starts in summer. Automatic turns the seasons on their own, summer, autumn, winter, spring,
**thirty minutes of the view being open, visible and running each** (two hours for the year). **Pause stops only the season clock** and keeps its time left; Automatic weather keeps running. Choosing a season holds it until you
choose Automatic, which starts a **fresh thirty minutes from the season showing at that moment** (it does not go back to summer). A change is a three-second cross-fade (at once under reduced motion). A season recolours the
*outdoors only*: fresher greens in spring, golden and browner foliage and ground in autumn, snow-covered ground and frosted foliage in winter, and crops that turn pale olive in winter. Offices look
the same in every season. Where a district is mostly water, sand or paving (the Coast, the Harbour, much of the Metropolis) there is little foliage to change, so **spring and summer look much alike there** (only the greens differ); autumn and winter differ clearly everywhere. In winter a **warm** district tint that lies over meadows and forest floors (the Autumn Wildwood's orange, the Metropolis's tan) is drawn cool and neutral, so snow reads as snow there. A cool, green or grey tint is left as it is, so **River Valley Farmland keeps an olive-cream winter ground, and a mild warm/cool seam remains where it meets the Highlands**.

**Weather** is **Automatic** (the default) or one manual choice for the whole world: **Clear**, **Rain**, **Light snow** or **Heavy snow**. **A manual choice overrides
the automatic rules in every region and every season** (Heavy snow in summer is allowed; Automatic never draws it there). Under Automatic the world has
five weather regions (City, Coast, Woodland, Highlands and Farmland by district; a world with no districts is split into three bands, west, middle and east, when it is at
least 96 columns wide, and is one region otherwise, which uses the City row). Each region draws its own next weather every **two to five minutes** from a seeded schedule. What a region may
draw depends on the season and its kind of region, with these weights (a draw picks a kind with probability weight / total):

| Season | City, Coast, Woodland, Farmland | Highlands |
| --- | --- | --- |
| Spring | clear 5, rain 3 | clear 4, rain 3, light snow 2 |
| Summer | clear 5, rain 2 | clear 5, rain 2 |
| Autumn | clear 4, rain 3 | clear 4, rain 3, light snow 2 |
| Winter | City and Farmland: clear 5, light snow 3. Coast: clear 5, light snow 2, **rain 2**. Woodland: clear 4, light snow 3, heavy snow 2 | clear 3, light snow 3, heavy snow 3 |

So a winter Coast can still rain, and **Automatic heavy snow happens only in winter, in the Woodland and the Highlands**. A region never has more than **two non-clear draws in a row**: the next draw is then
clear, so every region returns to clear. The seed is a fixed constant of the product (not random per install), so the same history gives the same weather.
Where two regions meet, the weather's **strength** (its veil and its particles) blends over about **four tiles into each side** of the border, so a clear region beside a wet one shows some of the neighbour's weather
within four tiles of the border, and its interior (beyond four tiles) has no particles and no veil. That ramp softens the strength; **the border itself can still run in a straight line**, because
the regions are whole districts or bands. Weather is a set of particles drawn over the *outdoors only*, **one pool of at most 520 particles for rain, light snow and heavy snow together**
(shared in proportion to the regions showing each kind, each share rounded down, so three kinds shown equally are 173 + 173 + 173 = 519, and one kind alone gets its whole capacity: rain 520, light snow 300, heavy snow 520).
A particle over an indoor tile or over empty ground is not drawn (a rain streak that would run into one is cut at its edge), so offices stay dry. Particles move with the world when you
pan; when you zoom, which particles are drawn is still decided by the world tile under each one, but the pattern is re-seated. A light-snow flake sits on a small cool shadow so it reads on pale
ground; heavy snow has larger flakes and a denser veil. While it rains or snows a faint cool veil also lies over the outdoors, so even a still frame reads as weather. Seasons and
weather are decoration: never a forecast, and never a sign of what an agent or a Mission is doing.

**What it looks like.** Four pictures of the built office, over a synthetic fixture office state (not a live provider); each caption bar is the state the page itself reported. Capture details, hashes, the clock of each picture and the limits are in [the provenance note](../assets/pixel-world/seasons-and-weather.md).

| Autumn Wildwood in the four seasons | River Valley Farmland in the four seasons |
|---|---|
| ![The Autumn Wildwood in spring, summer, autumn and winter](../assets/pixel-world/seasons-autumn-wildwood.png) | ![River Valley Farmland in spring, summer, autumn and winter, with pale-olive crops in winter](../assets/pixel-world/seasons-river-valley-farmland.png) |
| Season held on each, weather Clear. Captions are the state the page reported. Built app, synthetic fixture office state, real time. | The same four seasons over crops, barns and fences. Built app, synthetic fixture office state, real time. |

| Light and heavy snow | Regional weather changing on its own |
|---|---|
| ![Silver Pine Highlands in winter: clear, light snow, heavy snow, and autumn rain](../assets/pixel-world/weather-light-and-heavy-snow-highlands.png) | ![The Highlands go from light snow to heavy snow and back to clear while other regions change](../assets/pixel-world/weather-regional-transition-highlands.png) |
| Manual weather choices over the same ground. Built app, synthetic fixture office state, real time. | Automatic weather in a held winter, driven by the real scheduler on an **accelerated test clock**: +335 s of scheduler time in 2 s of wall time; each frame states its own elapsed time. Built app, synthetic fixture office state. |

| Two regions, two different weathers at once |
|---|
| ![Silver Pine Highlands in heavy snow beside clear River Valley Farmland, in one frame with the border in view](../assets/pixel-world/weather-two-regions-at-once-highlands-farmland.png) |
| Real time, no test clock: Automatic weather in a held winter on a fresh profile; the real seeded scheduler drew heavy snow for the Highlands and clear sky for Farmland at the same moment. Built app, synthetic fixture office state. A mild warm/cool seam remains at this border (Farmland keeps an olive-cream winter ground). |

**Reduced motion.** With the operating system's reduced-motion preference on, what moves stops and what fades is instant: weather particles are held as one still frame, and lighting,
seasons and weather change **at once** instead of cross-fading (the Clock shows the nearest still stage rather than a gradual ramp). **The clocks do not stop**: a scheduled season or weather change still
happens on its schedule, and is simply shown at once.
Turning the preference on while the view is open holds the rain where it is and finishes, at once, any season or weather fade that is already running; turning it off again restarts nothing.

**What is saved.** Only your choices and where the two schedules stand, as a compact record of whole numbers in `city-presentation.json` under the DI state root, **at most 256 bytes**
(about 130 measured): a version, the lighting and traffic modes, the season (mode, season, paused, time left) and the weather (mode, the fixed seed and, for each of the five regions, its
kind, its step in the schedule, its wet-draw streak and its time left). Nothing else is saved: no time of day, no particle, no position, nothing about a Mission or an agent. A saved record from
before seasons existed is migrated on load (lighting, traffic and weather are kept; the seasons start Automatic from summer) and reading it writes nothing; the next choice writes the new form. A saved
record that does not match the contract is ignored whole and the defaults stand. The record is checked before it is written and again when it is read. A choice is saved at once; a change the
schedules make is saved after at most one save every two seconds; a running clock is checkpointed at most once a minute and only if it actually advanced; a hidden view writes nothing,
and hiding it saves once more.

**How this is tested, and what that does and does not show.** The engine (lighting level math, the indoor/outdoor
mask, the lamp lights, the particle field, the per-frame caps) is tested directly, including on the shipped
metropolis world, with exact assertions of every bound. A real-browser check mounts the actual App from source in
headless Chrome over the shipped world with a labelled *fixture* office state: it opens the panel by keyboard,
compares canvas pixels (day is pixel-for-pixel the original look; night is darker while the lit things stay lit;
weather adds pixels and leaves none behind when cleared), saves through the real persistence module under a temp
store and reloads. Seasons and regional weather add exact tests of every bound (also pinned from Python), independent per-tile checks of the region edge written from its definition rather than from the
code under test, deliberately wrong versions of the code that those checks must fail, and combined loads (a held winter, Automatic regional weather with rain, light snow and heavy snow at once, night and traffic). That is integration over source, **not** production-package proof, and not evidence of a live
provider. The weather's veil and the indoor lighting depend on the world's tile types, so a world that colours its
floors differently will light differently.

### Moving traffic

**Traffic** is the third choice in the City panel: **Off**, **Low** or **Normal**. It brings the world's OWN parked vehicles to life: the
68 taxis, sedans, hatchbacks, SUVs, police cruisers, fire engines and ambulances that the metropolis already places on its streets, facing the
way their lane runs, drive the streets they stand on. It adds no new art and no new road: the lanes are derived from the road tiles
(three-wide bands with a dashed centre, zebra crossings across them, and the three-by-three intersections where two bands cross), and every
vehicle is drawn with the world's own `VEHICLE_*` sprite. **Off** is exactly the parked street as it always was. **Low** lets up to 24 drive
and the rest stay parked; **Normal** lets up to 96, which here means every one of the 68. While a vehicle drives, its parked copy is simply not
drawn (the layout itself is never touched).

How it behaves, and what that does and does not mean:

- Vehicles **stop at a stop line** before every intersection and crossing, wait their turn, and enter only when the whole intersection or
  crossing and the tile beyond it are free. They queue behind one another and turn right, turn left or go straight at an intersection by a
  seeded choice (there is no path search and no destination). Two vehicles can never hold the same tile.
- **People have priority.** A vehicle never enters a tile that a resident, pet or real agent stands on or is about to step onto, and never
  one adjacent to a real agent. If someone steps into a vehicle anyway it gives way at once: it fades out and comes back later. Vehicles never
  change what anyone can walk on, so decoration can never block a real agent.
- **A vehicle that looks like an ambulance, a fire engine or a police cruiser is an ordinary car.** It has no siren, no flashing, no priority
  and no meaning; it obeys every stop and gives way to people exactly like a taxi. Pointing at any vehicle labels it "Decorative traffic".
- **Reduced motion** shows every vehicle at the place it is parked and holds it still; with the preference on, a vehicle still gives way to
  anyone who steps into it. (In the shipped world the street at rest is the same picture as the parked street: measured, below.)
- **Nothing here is saved or simulated while the view is closed.** A reload, restart or reopened tab rebuilds the same vehicles, with the same
  ids, from the same seed at the places they were parked: no vehicle is ever duplicated, and no continuity is claimed. A hidden tab advances
  nothing, and there is no catch-up. Only the traffic CHOICE is saved.
- Only the DI office runs traffic; ordinary Pixel never does.

What stays parked, and the limits: vehicles in parking lots (those facing up or down) and the angled `DETAIL_TAXI` and `DETAIL_VAN` props stay
exactly where they are. Those props stand in some lanes and cut them into shorter stretches, and each of the four intersections on the
row-39 street has a parked taxi inside it, so those four are not usable intersections (12 of 16 are). A vehicle that reaches the end of a
stretch waits about a second, fades, and re-enters at its parking place about a second and a half later, so the street is busy but not a
closed loop. A vehicle that has been unable to move for 30 seconds is removed and re-enters (a gridlock valve; it has not opened in any run).

How this is tested, and what that does and does not show. The engine is tested directly on a small synthetic crossroads (stop lines and
dwell, atomic claims of an intersection, lawful turns, queues, a pedestrian on the crossing, a pedestrian about to step onto it, a real agent
beside the lane, someone stepping into a vehicle, reduced motion, the cap, determinism, and that an ambulance behaves exactly as a taxi) and
on the shipped metropolis (over ten simulated minutes no tile was ever claimed twice and no vehicle left a lane; a real agent standing on a real
road held vehicles back; the layout, blocked tiles and the seeded residents and trains were byte-identical with traffic on and off).
A real-browser check mounts the actual App from source over the shipped world with a labelled *fixture* office state and proves the motion on
the canvas itself: it matches each vehicle's real sprite file, pixel for pixel, against the canvas at the position its pose predicts, and finds
the same vehicle at a different place 0.7 s later. That is integration over source, **not** production-package proof, and no part of it is
evidence of a live provider.

### All four together: the Map, the limits and what was measured

**The Map.** The Map shows the same city from above: residents as job-coloured dots, the two trains as teal bars and, new with
traffic, every **visible vehicle as a small box** of one neutral colour, its own footprint, never smaller than two pixels. The boxes
are drawn from the same vehicles the canvas draws, at the Map's own slow redraw (twice a second; every two seconds under reduced
motion), so a vehicle on the Map can be a moment behind the one on the canvas. A vehicle that is driving is not also drawn as parked
furniture, so a vehicle never appears twice: it is parked furniture in the Map's static layer, or a box, never both. **It does not follow
that every vehicle is shown at every moment.** A vehicle that has reached the end of its stretch waits, fades and returns (a second or two), and
while it is away it has neither a parked copy nor a box: forty seconds into a run with traffic Normal, 38 pieces were still parked and 55 boxes were on
the Map, of the 106 vehicle pieces the world places, so the other 13 of the 68 driving slots were away. Held still (reduced motion) every vehicle is shown, parked or as a
box. With traffic Off the Map is exactly the parked street. The boxes are not coloured by kind, so nothing on the Map looks like an emergency vehicle.

**Hidden, closed, paused and reduced.** While the tab is hidden nothing advances. When it is shown again the residents and the subway are
caught up by **at most 20 seconds, in at most 200 steps**, however long it was hidden; the traffic, the seasons and the weather are **not** caught up
(a street that was hidden is simply where it was, and the seasons and weather count only visible running time). **Nothing runs while the page is closed, the computer is asleep or it is rebooted:** no
simulation time passes, and nothing is claimed about what the city did meanwhile. Opening the Map covers the canvas and the canvas stops
drawing; the world underneath keeps advancing, and the Map shows it. With reduced motion everything is held still (night shown at once,
the rain on one frame, the vehicles where they are parked, the residents and trains where they stand); a resident, a vehicle or a train
still gives way to a real agent.

**Reload and restart.** The residents and trains are saved while the office is open (see above) and a reload restores each resident once and
each rider aboard its own train, and the journeys carry on: a restored rider alights where it was going, and is recorded as having boarded in
an earlier run. The traffic is **not** saved: it is rebuilt from its seed, the same vehicles with the same ids at the places they were parked,
and never continues what it was doing. Only your city choices are saved: lighting, traffic, the season and the weather, with where each schedule stands. A snapshot that does not belong to this
world, these residents or these journeys is refused whole and the city starts fresh.

**What it costs, as measured.** These were measured in the real App in the installed Chrome on one Mac, over the source with Vite's
development server (an unminified build: **not** the production package, whose own measurement is separate), against the shipped world with the
fixture office state. The loads: *ordinary* (145 residents, 24 of them riding the subway, 68 vehicle slots, rain and night, 16 fixture agents),
*crowded* (the population at its cap of 200, and 24 more agents put on the streets among them) and *maximal eligible* (the same, the camera at its widest: the largest values this world can reach, 200 residents, 24 riders, 2 trains, 68 vehicle slots and 520 rain particles).
Figures are rounded; the run's logs have the exact ones.

| What | Bound | Ordinary | Crowded / maximal eligible |
| --- | --- | --- | --- |
| Residents | 200 | 145 | 200 |
| Riders (and aboard at once) | 24 (32) | 24 (up to 16 seen) | 24 |
| Trains | 4 (2 on this line) | 2 | 2 |
| Vehicles | 96 (68 slots in this world) | about 52 on the street, of 68 | about 52 of 68 |
| Weather particles (rain, light snow and heavy snow together, one pool) | 520 | measured: 519 with all three kinds shown (173 + 173 + 173); 520 with heavy snow alone | the same: 519 and 520 |
| Lights lit per frame | 96 | at most 49 at any of twelve camera positions of a normal viewport (39 in an earlier run: it depends where the agents stand) | the same twelve positions; 96 only on a synthetic frame no camera reaches (the whole world at a quarter of the scale) |
| Path searches per tick | 4 | 4 | 4 |
| Saved snapshot | 65,536 bytes | about 7 KB | about 10 KB |
| Saved city choices | 256 bytes | 130 bytes | 130 bytes |
| Timed `requestAnimationFrame` callbacks, **per callback** (first labelled "Game-loop frame (simulation plus issuing every draw call)": the timing wrapper times every callback registered by anyone, so it cannot show which is the game loop, and two callbacks share each requestAnimationFrame timestamp; see below) | 50 ms at the 95th percentile (declared for this per-callback sample) | mean 3.5 ms, 95th percentile 5.6 ms | mean 3.6 ms, 95th percentile 5.6 to 5.9 ms |
| One ordinary simulation tick | 8 ms at the 95th percentile | mean 0.1 ms | mean 0.2 ms |
| Elements added to the page | 120 | 44 (the open City panel) | not measured with the panel open |
| Catch-up of a long absence (20 s, 200 steps) | 600 ms | about 5 ms | about 5 ms |

Night, rain and traffic together add nothing measurable to the **per-callback** figures: with lighting Day, weather Off and traffic Off the same world's
per-callback cost is the same (mean 3.6 ms, 95th percentile 5.5 ms, against 3.5 and 5.6 with them on). That comparison is a **three-effects delta**: the
residents and the subway were running in BOTH arms, so **the cost of the residents' refinement was never isolated** and is not claimed. The figures are the
main thread's cost of **issuing** the drawing, per callback, not the browser's rasterising of it.

**What the callbacks sharing one timestamp cost together.** The per-callback figures above are NOT the cost of the callbacks that run at one timestamp, and the
first label on that row overstated what the sample is. In the built application two callbacks share each `requestAnimationFrame` timestamp. A separate,
one-run measurement of the BUILT application (the fixture viewer serving the built bundle, installed Chrome 155, 1600 by 1000, no hooks) grouped the callbacks
by identical timestamp and summed each group. These **per shared-timestamp callback sums**, 95th percentile, were **29.4 ms** with night, rain and traffic on
and the camera on the street grid (29.5 ms by nearest rank), **33.1 ms** with the Map open, and **5.7 ms** with reduced motion, over about **59.92, 57.25 and
60.00 distinct timestamps per second** (about 120 per second is only the callback count). **These sums are main-thread callback time only: they exclude
layout, raster, the GPU and any other work, and they make no presentation claim, no display-frame-rate claim and no GPU claim.** The measurement shows shared
timestamps and nothing more; it does not show that one timestamp is one presented frame. The sums are **materially higher** than the 3.5 ms and 5.6 ms above,
and in the visible and Map runs some of them exceed 16.7 ms, which is the spacing between distinct timestamps at about 60 per second (60 of 719 sums and 40 of
458, arriving at exactly 5 per second, the cadence of the furniture rebuild below: a matching rate, not an attribution). All three 95th percentiles are **below
the 50 ms number**, which was declared for the per-callback sample, so this compares a different quantity with it; no sum was over 50 ms, and there was no long
task and no page error. Nothing here relaxes a limit or changes the engine: it is a correction to what the sample was called. The sample still does not say
which callback is the game loop.
Three things are worth saying plainly. First, **the vehicle bound of 96 cannot be reached in this world**, which has 68 vehicles that can drive.
Second, one cost in these figures is **not part of the living city and is unchanged by it**: every 0.2 s the office rebuilds the instances of its
furniture (ten thousand pieces in this world), about 24 ms in the development build, the same with every feature on or off. It is the inherited contributor
these runs identified (the slowest samples are about the size of one such rebuild, and in the built application the shared-timestamp callback sums over 16.7 ms arrive at the same 5
per second); this document does not claim it is the only one. Third, residents may cross a road anywhere, not only at a crossing; vehicles give way to them (over five simulated
minutes with 145 residents, someone stepped into a vehicle 20 times and the vehicle faded out and came back, vehicles were held back 968 times
for people on the road ahead, none was ever stuck, and the street drove 98.6 per cent as many tiles as with nobody on foot).

**How this is tested.** The refined residents and subway are tested on the shipped world: 24 riders of three journey kinds, each plan checked
against its routine and ride plan; every rider walking to a platform, waiting, boarding, riding and alighting at the stations its plan names, in
the plan's order, over half an hour of simulated time; an excursion resumed after a reload on its second and third ride; platform tiles dealt
apart (two riders waiting on one tile for 0 seconds in half an hour; a platform tile held by any two residents at once for 242 seconds in half
an hour, against 227 seconds in ten minutes before the refinement: what is left is one resident walking along a one-tile platform past another); the posted and roaming dwell profiles;
and the snapshot's binding to the journey plan. The four together are tested as one OfficeState: residents and trains identical whatever the
lighting, weather and traffic are set to, traffic identical whatever the lighting and weather are, the Map accounting for every vehicle in every
traffic setting, and the search, hub, route, routine and aboard bounds at the population cap. In the real App, the loads above, reduced motion
(nothing of the four moves in ten seconds), a hidden tab and the bounded resume, twelve settings changes in a row, a reload mid-journey, and a
snapshot of another plan being refused. That is integration over source with a labelled fixture office state, **not** production-package proof, and
not evidence of a live provider.

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
