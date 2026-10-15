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
2. Settings → **Import Layout**, and pick `expanded-office.json`.
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
  `reviewer_agent`, `protocol_token`.
- **What it writes.** Registration writes only its registry. The adapter writes only its own
  discovery file. Neither ever writes a store.
- **Routes.** `GET /catalog`, `GET /diagnostics` and `GET /healthz`, and nothing else. They
  are loopback-only (`127.0.0.1`), behind one bearer token compared in constant time, with
  exact `Host` and `Origin` checks. There is no CORS header, no cookie and no 5xx.
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
