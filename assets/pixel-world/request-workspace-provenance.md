# Request workspace pictures: provenance

Four pictures of the Pixel Agents DI office's request workspace (Ask, Requests, wider reading) and of the sidebar request card, taken on 22 October 2026 for the README subsection "An optional in-world request: the Codex Orchestrator".

**These are synthetic owned-fixture captures, not owner-supplied captures and not live Mission work.** Every conversation, proposal and request in them is a labelled synthetic fixture, and the workspace says so in the frame ("Synthetic": "Conversation, proposals and results here are synthetic; no approval given here is real"). Nothing in them was approved, sent, started or delivered. They are not an acceptance by anyone.

## The pictures

- `request-workspace-ask.png` (241241 bytes): Ask: the one context area, the drafting note once, short You / Assistant labels, an older reply with its technical block folded away, and a current reply with its two questions; published file sha256 `14de6c74311d738f27841b5854d422e588d58f71bd803af94fd9e3cc6b0efa2b`
- `request-workspace-requests.png` (318423 bytes): Requests: the list and the open request’s first layer (its title, its status and next step, and one closed “Exact references (technical)” disclosure), then the proposal; published file sha256 `d9480c0453429b11de7ba778d21e7778ed4ed1dcc7706bb71a5e511d073ad9ff`
- `request-workspace-wider.png` (355020 bytes): Wider reading: the list beside the open request (960px pane); published file sha256 `6e96f5d013449683ea755e783127d9985e9132cfa3f2ef3a14cc921d19d2d6f1`
- `request-workspace-sidebar-card.png` (47246 bytes): the sidebar request card: its counts each name what they count; published file sha256 `d60727553d7adcfcc2005ea7cec2d84c9a7cb14143ab367f86067a100c055731`

## Which build they come from

All four are from **the final package** of the request-workspace polish: the immutable standalone DI package built from the frozen source with `build:extension` (production), `build:webview` and `scripts/build-di-package.mjs`, made read-only afterwards. Runtime files that produced the pixels (contained-file sha256; the tarball's own hash is not relied on):

  - `dist/di-cli.js` sha256 `6a2bc30fed16a1efab479bd3907af41534aeed200256b36b8555f1332b262432`
  - `dist/webview/index.html` sha256 `6fd5d0b762d3a44cda5ed454af79b9137eed00fbda680f7748e1e24aee2e6fc6`
  - `dist/webview/assets/index-CM6dCxZk.js` sha256 `f429d359849c1de00ce2c003264fbc8c043d05fa6c2433168341c8d47d765a4f`
  - `dist/webview/assets/index-5FMviMs2.css` sha256 `a3494cb2040dab6760a7317a0a159a05c016692c57b5365c32e214b23d334eb9`

Frozen source identity: the filesystem manifest sha256 `053ef0161d107d916b6f0e6c4b0687753dcf20a5b6e1284c365149094f46d25c` (every file of the worktree except `.git`, `node_modules`, `dist`, `.herd` and caches; no Git was used to make it). The pictures were captured from this package; the README, the guide, the changelog and this file were then edited and the four pictures added, none of which is part of the package runtime.

## How they were taken

Headless Google Chrome 155.0.8059.85 driven by playwright-core 1.62.0, device pixel ratio 2, 1600 x 1000 CSS pixels, a fresh state directory and a freshly seeded synthetic request panel per run. Each picture is an **element screenshot**: the workspace panel, or the sidebar request card, and nothing else, so there is no browser chrome, no address bar and no access token in any of them. The capture script also scans each element's rendered text and refuses to continue if it holds a token, a loopback address or a private path.

## The other published pictures were inspected, and none needed a new capture

Each picture under `assets/pixel-world/` was scanned for how much of it is drawn in the side panel's surface colours (`#151824`, `#1c2030`, `#242a3d`, `#2c3245`, `#262d4a`). World and canvas pictures contain almost none. The four that contain side-panel or inbox UI were then looked at, region by region, for anything this change altered (the request card's count words, the request workspace):

| Picture | Share of side-panel colours | What it shows | Original build attribution (kept) |
|---|---|---|---|
| `operational-panel-inspector.png` | 96.3% | unchanged: the Mission details view, no request card or workspace | the earlier side-panel release (see `operational-panel.md`) |
| `operational-panel-overview.png` | 91.2% | unchanged: the sidebar card shows the disabled variant, no counts | the earlier side-panel release (see `operational-panel.md`) |
| `reception-offices-inbox-decision.png` | 94.1% | unchanged: the Reception inbox: its own coverage lines and cards, which this change did not alter | the final package `build-F3b-4052b0e6-4989dbb9` (see `reception-offices-provenance.md`) |
| `reception-offices-inbox-top.png` | 94.0% | unchanged: the Reception inbox: its own coverage lines and cards, which this change did not alter | the final package `build-F3b-4052b0e6-4989dbb9` (see `reception-offices-provenance.md`) |

The remaining 25 pictures are world or canvas pictures (the office, its zoom buttons, legend and toolbar) with no side panel, inbox or request workspace in the frame. They keep their original attribution and are not relabelled.

## After the pictures were taken: the covering-overlay fix, and why none of these pictures changed

A later source change raised the workspace's layer **only where it covers the window**, so the office's decorative ambient legend no longer letters over its text (the one-line stylesheet change `.di-mr[data-covering=true]{z-index:30}` to `44`). It was built into a NEW immutable package `build-PF2-3203e90c` (frozen source manifest sha256 `3203e90c83d45793fb454876e4caa0b66ebb1e4e4cc2dcdca5d61b1a375fc069`); `build-PF-053ef016` is preserved. Compared file by file, the two packages hold **3,616 identical files out of 3,620**; the four that differ are the stylesheet (one declaration), the script (renamed only: the same content hash `f429d359…`), `index.html` (the two asset file names) and `PACKAGE-INVENTORY.txt` (the same names). The server bundle `dist/di-cli.js` is identical. The CSS is therefore **not** byte-identical between the two builds, and these four pictures are **not** presented as captured from the new build: they keep their exact attribution to `build-PF-053ef016` and were not re-captured, because none of them shows a covering workspace or the legend over the workspace (they show the workspace beside the side panel at 1600 x 1000 and the sidebar card, where the changed rule does not apply).

## What these pictures do and do not show

- Each picture is one instant of one synthetic scene, not a promise about every moment.
- The workspace is shown at 1600 x 1000. Smaller windows (it covers the window below 1020 px with the side panel shown, below 600 px with it hidden) are checked by the smoke tests, not pictured here.
- Nothing here is Mission success, delivery or live concurrency. Acceptance with real identities is a separate, later step.
