---
name: DI World
description: The DI office scan face, a calm dark operator chrome with one violet accent (Pixel Agents DI office, browser)
colors:
  di-bg: "#0d0f1b"
  di-surface: "#141726"
  di-surface-deep: "#080a12"
  di-control: "#1f2333"
  di-control-hover: "#2d3348"
  di-selected: "#231d52"
  di-line: "#262a3d"
  di-line-soft: "#1b1e2d"
  di-edge: "#737a91"
  di-black: "#000000"
  di-details-scroll-thumb: "#737a91"
  di-details-scroll-track: "#1f2333"
  di-text: "#e6e2d6"
  di-muted: "#8b919e"
  di-faint: "#9a9aa6"
  di-neutral: "#c6c6cf"
  di-link: "#a9a6ff"
  di-on-accent: "#ffffff"
  di-accent: "#552df0"
  di-accent-b: "#746fff"
  di-accent-hover: "#6236f5"
  di-focus: "#9d99ff"
  di-ok: "#89d185"
  di-run: "#6fb2ff"
  di-perm: "#e6c22e"
  di-warn: "#ffa94d"
  di-bad: "#ff8a8a"
  di-ok-tint: "#21272f"
  di-run-tint: "#1b243e"
  di-perm-tint: "#2a2624"
  di-warn-tint: "#2f2426"
  di-bad-tint: "#2e1d2c"
  di-scan-bg: "#0d0f1b"
  di-scan-canvas: "#0d0f1b"
  di-scan-well: "#141726"
  di-scan-well-edge: "#262a3d"
  di-scan-edge: "#737a91"
  di-scan-heading: "#e6e2d6"
  di-scan-raised: "#1f2333"
  di-scan-selected: "#141726"
  di-scan-line: "#262a3d"
  di-scan-text: "#e6e2d6"
  di-scan-muted: "#8b919e"
  di-scan-yellow: "#ffe60a"
  di-scan-needs: "#f17f08"
  di-scan-building: "#3e90f7"
  di-scan-ok: "#04c348"
  di-scan-bad: "#ef5b59"
  di-scan-needs-fill: "#4c2605"
  di-scan-building-fill: "#001f62"
  di-scan-ok-fill: "#043018"
  di-scan-bad-fill: "#3a1418"
  di-scan-needs-line: "#f6871a"
  di-scan-building-line: "#0376f7"
  di-scan-ok-line: "#04c348"
  di-scan-bad-line: "#ef5b59"
  di-scan-badge: "#231d52"
  di-scan-attn: "#e6c22e"
  di-scan-icon: "#e6e2d6"
  di-scan-logo: "#552df0"
  di-scan-accent: "#552df0"
  di-scan-accent-hover: "#6236f5"
  di-scan-underline: "#746fff"
  di-scan-ink: "#1a1408"
  di-scan-chip: "#1e2230"
  di-scan-needs-chip: "#3a2418"
  di-scan-building-chip: "#1a2740"
  di-scan-ok-chip: "#15281c"
  di-scan-bad-chip: "#3a1c22"
  di-scan-diff-add-bg: "#122421"
  di-scan-diff-add: "#74e08c"
  di-scan-diff-del-bg: "#1e131f"
  di-scan-diff-del: "#e47084"
  di-scan-code: "#e4e7f2"
  di-scan-code-key: "#c4b6ff"
  di-scan-code-fn: "#7fd7ff"
  di-scan-code-str: "#8ee0b0"
  di-scan-code-note: "#9aa3b8"
  di-scan-hunk: "#b7a6ff"
  di-scrim: "color-mix(in srgb, #080a12 80%, transparent)"
typography:
  title:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "18px"
  read:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "14px"
  body:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
  small:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "12px"
  label:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "11px"
    letterSpacing: "0.08em"
  code:
    fontFamily: "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
    fontSize: "12px"
  pixel:
    fontFamily: "'FS Pixel Sans', sans-serif"
rounded:
  di-radius-sm: "6px"
  di-radius: "8px"
  di-radius-box: "10px"
  di-radius-card: "12px"
  di-radius-pill: "999px"
spacing:
  di-space-1: "4px"
  di-space-2: "6px"
  di-space-3: "8px"
  di-space-4: "10px"
  di-space-5: "12px"
  di-space-6: "14px"
  di-space-7: "18px"
  di-layout-1: "24px"
  di-layout-2: "32px"
  di-layout-3: "48px"
components:
  button-primary:
    backgroundColor: "{colors.di-scan-accent}"
    textColor: "{colors.di-on-accent}"
    rounded: "{rounded.di-radius-sm}"
    height: "32px"
    padding: "0 12px"
  button-primary-hover:
    backgroundColor: "{colors.di-scan-accent-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.di-scan-text}"
    rounded: "{rounded.di-radius-sm}"
    height: "32px"
    padding: "0 12px"
---

# DESIGN.md — DI World design system

## Overview

The DI office's scan face: a calm, dark operator chrome with one violet accent, flat surfaces, Inter, and status that is always a word plus a shape
plus a colour. The mode for every DI surface is **Operate** (see `PRODUCT.md`): scanability, consistency and the real usage scene outrank expression.

**Source of truth:** the **107 `--di-*` declarations** on `:root` in `integrations/pixel-agents/webview-ui/src/index.css`. The frontmatter above is
derived from them: colours keep their names, and `var()` aliases are resolved to the literal they compute to (this format does not allow one
primitive to reference another). If this file and the stylesheet disagree, the stylesheet wins and this file is out of date. Every declaration is
placed in the token map below. One frontmatter entry is not a `--di-*` token: `typography.pixel` is `--font-pixel` (`index.css:203`), the pixel
world's base face (`@apply font-pixel`, `index.css:250`). It carries only `fontFamily`, because that face has no size token. It is declared so that
the declared faces match what the code uses; it does not admit the face into DI chrome.

Two visual worlds share the page and must not be mixed:

- **The DI chrome** (rail, Home, Mission details, Reception, New Mission, the request workspace, DI dialogs) uses only `--di-*` tokens and Inter.
- **The pixel office** (the world canvas, its toolbar, the City and Map overlays, and the shared `components/ui/*` such as `Modal`) keeps its own
  pixel chrome: `--pixel-*` tokens, FS Pixel Sans, hard 2px offset shadows, square corners. The pass C block in `index.css` (`.di-world-home`)
  converges only the world's triggers onto DI tokens.

This file separates **the required bar** (rules every change must meet) from **what is enforced today** (exact lint rules and tests; see Do's and
Don'ts). Changes still in review are labelled; "accepted" names a stage's Lead acceptance, which is scoped to that stage's frozen packets and not to the live tree. How to run the design workflow (Impeccable) is in `docs/design-workflow.md`.

### Token map (all 107 declarations; 104 until B2b added the three layout steps; B2b, accepted)

| Source token (`:root`) | Source value | In this file |
|---|---|---|
| `--di-bg` | `#0d0f1b` | colors.di-bg |
| `--di-surface` | `#141726` | colors.di-surface |
| `--di-surface-deep` | `#080a12` | colors.di-surface-deep |
| `--di-control` | `#1f2333` | colors.di-control |
| `--di-control-hover` | `#2d3348` | colors.di-control-hover |
| `--di-selected` | `#231d52` | colors.di-selected |
| `--di-line` | `#262a3d` | colors.di-line |
| `--di-line-soft` | `#1b1e2d` | colors.di-line-soft |
| `--di-edge` | `#737a91` | colors.di-edge |
| `--di-black` | `#000000` | colors.di-black |
| `--di-details-scroll-thumb` | `var(--di-edge)` | colors.di-details-scroll-thumb |
| `--di-details-scroll-track` | `var(--di-control)` | colors.di-details-scroll-track |
| `--di-text` | `#e6e2d6` | colors.di-text |
| `--di-muted` | `#8b919e` | colors.di-muted |
| `--di-faint` | `#9a9aa6` | colors.di-faint |
| `--di-neutral` | `#c6c6cf` | colors.di-neutral |
| `--di-link` | `#a9a6ff` | colors.di-link |
| `--di-on-accent` | `#ffffff` | colors.di-on-accent |
| `--di-accent` | `#552df0` | colors.di-accent |
| `--di-accent-b` | `#746fff` | colors.di-accent-b |
| `--di-accent-hover` | `#6236f5` | colors.di-accent-hover |
| `--di-focus` | `#9d99ff` | colors.di-focus |
| `--di-ok` | `#89d185` | colors.di-ok |
| `--di-run` | `#6fb2ff` | colors.di-run |
| `--di-perm` | `#e6c22e` | colors.di-perm |
| `--di-warn` | `#ffa94d` | colors.di-warn |
| `--di-bad` | `#ff8a8a` | colors.di-bad |
| `--di-ok-tint` | `#21272f` | colors.di-ok-tint |
| `--di-run-tint` | `#1b243e` | colors.di-run-tint |
| `--di-perm-tint` | `#2a2624` | colors.di-perm-tint |
| `--di-warn-tint` | `#2f2426` | colors.di-warn-tint |
| `--di-bad-tint` | `#2e1d2c` | colors.di-bad-tint |
| `--di-scan-bg` | `var(--di-bg)` | colors.di-scan-bg |
| `--di-scan-canvas` | `var(--di-bg)` | colors.di-scan-canvas |
| `--di-scan-well` | `var(--di-surface)` | colors.di-scan-well |
| `--di-scan-well-edge` | `var(--di-line)` | colors.di-scan-well-edge |
| `--di-scan-edge` | `var(--di-edge)` | colors.di-scan-edge |
| `--di-scan-heading` | `var(--di-text)` | colors.di-scan-heading |
| `--di-scan-raised` | `var(--di-control)` | colors.di-scan-raised |
| `--di-scan-selected` | `var(--di-surface)` | colors.di-scan-selected |
| `--di-scan-line` | `var(--di-line)` | colors.di-scan-line |
| `--di-scan-text` | `var(--di-text)` | colors.di-scan-text |
| `--di-scan-muted` | `var(--di-muted)` | colors.di-scan-muted |
| `--di-scan-yellow` | `#ffe60a` | colors.di-scan-yellow |
| `--di-scan-needs` | `#f17f08` | colors.di-scan-needs |
| `--di-scan-building` | `#3e90f7` | colors.di-scan-building |
| `--di-scan-ok` | `#04c348` | colors.di-scan-ok |
| `--di-scan-bad` | `#ef5b59` | colors.di-scan-bad |
| `--di-scan-needs-fill` | `#4c2605` | colors.di-scan-needs-fill |
| `--di-scan-building-fill` | `#001f62` | colors.di-scan-building-fill |
| `--di-scan-ok-fill` | `#043018` | colors.di-scan-ok-fill |
| `--di-scan-bad-fill` | `#3a1418` | colors.di-scan-bad-fill |
| `--di-scan-needs-line` | `#f6871a` | colors.di-scan-needs-line |
| `--di-scan-building-line` | `#0376f7` | colors.di-scan-building-line |
| `--di-scan-ok-line` | `#04c348` | colors.di-scan-ok-line |
| `--di-scan-bad-line` | `#ef5b59` | colors.di-scan-bad-line |
| `--di-scan-badge` | `var(--di-selected)` | colors.di-scan-badge |
| `--di-scan-attn` | `var(--di-perm)` | colors.di-scan-attn |
| `--di-scan-icon` | `var(--di-text)` | colors.di-scan-icon |
| `--di-scan-logo` | `var(--di-accent)` | colors.di-scan-logo |
| `--di-scan-accent` | `var(--di-accent)` | colors.di-scan-accent |
| `--di-scan-accent-hover` | `var(--di-accent-hover)` | colors.di-scan-accent-hover |
| `--di-scan-underline` | `var(--di-accent-b)` | colors.di-scan-underline |
| `--di-scan-ink` | `#1a1408` | colors.di-scan-ink |
| `--di-scan-chip` | `#1e2230` | colors.di-scan-chip |
| `--di-scan-needs-chip` | `#3a2418` | colors.di-scan-needs-chip |
| `--di-scan-building-chip` | `#1a2740` | colors.di-scan-building-chip |
| `--di-scan-ok-chip` | `#15281c` | colors.di-scan-ok-chip |
| `--di-scan-bad-chip` | `#3a1c22` | colors.di-scan-bad-chip |
| `--di-scan-diff-add-bg` | `#122421` | colors.di-scan-diff-add-bg |
| `--di-scan-diff-add` | `#74e08c` | colors.di-scan-diff-add |
| `--di-scan-diff-del-bg` | `#1e131f` | colors.di-scan-diff-del-bg |
| `--di-scan-diff-del` | `#e47084` | colors.di-scan-diff-del |
| `--di-scan-code` | `#e4e7f2` | colors.di-scan-code |
| `--di-scan-code-key` | `#c4b6ff` | colors.di-scan-code-key |
| `--di-scan-code-fn` | `#7fd7ff` | colors.di-scan-code-fn |
| `--di-scan-code-str` | `#8ee0b0` | colors.di-scan-code-str |
| `--di-scan-code-note` | `#9aa3b8` | colors.di-scan-code-note |
| `--di-scan-hunk` | `#b7a6ff` | colors.di-scan-hunk |
| `--di-font-ui` | `Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif` | typography.*.fontFamily (title, read, body, small, label) |
| `--di-font-code` | `ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace` | typography.code.fontFamily |
| `--di-text-label` | `11px` | typography.label.fontSize |
| `--di-text-sm` | `12px` | typography.small.fontSize, typography.code.fontSize |
| `--di-text-base` | `13px` | typography.body.fontSize |
| `--di-text-read` | `14px` | typography.read.fontSize |
| `--di-text-title` | `18px` | typography.title.fontSize |
| `--di-label-tracking` | `0.08em` | typography.label.letterSpacing |
| `--di-space-1` | `4px` | spacing.di-space-1 |
| `--di-space-2` | `6px` | spacing.di-space-2 |
| `--di-space-3` | `8px` | spacing.di-space-3 |
| `--di-space-4` | `10px` | spacing.di-space-4 |
| `--di-space-5` | `12px` | spacing.di-space-5 |
| `--di-space-6` | `14px` | spacing.di-space-6 |
| `--di-space-7` | `18px` | spacing.di-space-7 |
| `--di-layout-1` | `24px` | spacing.di-layout-1 (layout scale, B2b) |
| `--di-layout-2` | `32px` | spacing.di-layout-2 (layout scale, B2b) |
| `--di-layout-3` | `48px` | spacing.di-layout-3 (layout scale, B2b) |
| `--di-radius-sm` | `6px` | rounded.di-radius-sm |
| `--di-radius` | `8px` | rounded.di-radius |
| `--di-radius-box` | `10px` | rounded.di-radius-box |
| `--di-radius-card` | `12px` | rounded.di-radius-card |
| `--di-radius-pill` | `999px` | rounded.di-radius-pill |
| `--di-scrim` | `color-mix(in srgb, var(--di-surface-deep) 80%, transparent)` | colors.di-scrim |
| `--di-shadow-pop` | `0 20px 50px color-mix(in srgb, var(--di-black) 64%, transparent)` | Elevation & Depth |
| `--di-shadow-primary` | `0 0 0 1px color-mix(in srgb, var(--di-accent-b) 25%, transparent), 0 6px 16px color-mix(in srgb, var(--di-accent) 28%, transparent)` | Elevation & Depth |
| `--di-glow` | `0 0 0 2px var(--di-focus), 0 0 14px 4px color-mix(in srgb, var(--di-accent-b) 55%, transparent)` | Elevation & Depth |
| `--di-glow-peak` | `0 0 0 2px var(--di-focus), 0 0 20px 7px color-mix(in srgb, var(--di-accent-b) 72%, transparent)` | Elevation & Depth |

## Colors

Dark, flat surfaces with one violet accent. Every colour in DI chrome is a `--di-*` token; no new tokens and no raw colour literals.

- **Surfaces**: `di-bg` (page) < `di-surface` (wells, cards) < `di-control` / `di-control-hover` (controls). `di-surface-deep` is the deepest layer
  and the base of `di-scrim` behind dialogs. `di-selected` is the selected row or tab.
- **Lines**: `di-line`, `di-line-soft` between regions. `di-edge` is a control's edge and the scrollbar thumb.
- **Text**: `di-text` (body), `di-muted` and `di-faint` (secondary), `di-neutral`, `di-link`, and `di-on-accent` on the accent.
- **Accent and focus**: `di-accent` (the one primary action's fill), `di-accent-hover`, `di-accent-b` (underline and secondary accent),
  `di-focus` (every focus ring).
- **Status**: `di-ok`, `di-run`, `di-perm`, `di-warn`, `di-bad`, each with a `-tint` fill for pills and notices, always paired with a word and a shape.
- **Scan face roles** (`di-scan-*`): mostly aliases of the core palette, plus the Home status set (`needs`, `building`, `ok`, `bad`, each with `-fill`,
  `-line` and `-chip`), the diff colours and the code highlight colours.

## Typography

- **Face**: Inter (`--di-font-ui`), bundled with `@font-face` (pinned by `test/diInterFont.test.ts` and `test/diInterRendered.test.ts`); the
  system stack shows only while it loads. `--di-font-code` (system monospace) for code, ids, digests and refs; the scan face's Code Diff editor (its
  code and path) consumes it too (B2d, accepted: it had declared JetBrains Mono first, a face the product never shipped, so where that face is
  installed the editor's rendering changes). It is a system stack, so its metrics vary by platform: code is sized and measured in px, and a character
  count is per machine. FS Pixel Sans (`--font-pixel`, frontmatter `typography.pixel`, family only) is the pixel office's face, not DI chrome's.
- **Scale**: label 11px (uppercase, 0.08em tracking), small 12px, body 13px, reading 14px, title 18px. No other sizes in chrome.
- **Conformance today (a non-conformance, NOT a permitted exception):** existing DI chrome does not meet this scale. The first contained
  detector run (2026-11-05) found **54 font sizes off it**: 44 in the scan-face stylesheets (`diScanPanel.css`, `diScanPages.css`, largely their
  locked pixel-lock compositions) and 10 in the code review dialog, the operational panel and the request workspace. Each is itemised, with its
  disposition, in the local herd record `.herd/state/verification/d0/detector/DISPOSITIONS.md` (not committed; rerun the detector to reproduce the
  list). All are **OPEN**, to be resolved by the direction Travis selects. The rule above stands as written.
- **Direction B, B2b (accepted):** every size in the scan face's STAGE (Mission details, Code Diff, Comments, Evidence and the rail pages) is on this
  scale: the pixel-lock sizes there moved onto it, and the 11 px left there are uppercase, tracked labels. The scan face's other surfaces (Home, the
  rail) are unchanged, and the count above is NOT re-measured: it stands as the D0 detector's count until the next contained detector run. The stage's spacing: see Layout (the layout scale the Lead ruled on, and its residual non-conformances, 74 uses of 21 values).

## Layout

- **Spacing**: `di-space-1` … `di-space-7` = 4, 6, 8, 10, 12, 14, 18px. This is COMPONENT spacing: the space INSIDE one box or one control group (a box's
  padding, the gap between a group's items, an icon's inset).
- **Layout spacing (Direction B, B2b, accepted; a deliberate extension the Lead ruled on, 2026-11-05):** `di-layout-1` … `di-layout-3` = 24, 32, 48px,
  a scale DISTINCT from component spacing, for space that FRAMES a screen or SEPARATES its top-level regions: a screen's outer gutter, the separation
  between its top-level sections (bar, title, card, the next section, the action row), and the margin that sets a whole block off. Never inside a
  component.
  - *Why:* the component scale stops at 18 and cannot express layout. The scan face's stage used 14 different values between 19 and 48 (layout and component uses alike; only the layout uses were mapped); forcing
    48 to 18 would have been a destructive change made to satisfy a token rule. The rendered evidence (1280, 1440 and 1600) clustered them in three
    bands, so three steps, the fewest it supported.
  - *Mapping of the legacy values (layout uses only; each use classified in words first):* 20, 21, 22, 24 -> 24; 26, 27, 28, 29, 30, 32, 34 -> 32; 40, 48
    -> 48. The largest changes, 26 -> 32 and 40 -> 48, were measured at all three widths with no overlap and no overflow.
  - *Residual, recorded NON-CONFORMANCES (not on any scale), counted from the stylesheets after the extension: 74 uses of 21 values* in the stage:
    1, 2, 3, 5, 7 (18 uses), 11, 13, 15, 16, 17 (30 uses), and 26 uses of 19 to 32 that are component-internal (among them the stage bar's own padding,
    28 / 22 and Comments' 21 / 22 / 29, which positions the bar's controls that the header instrument holds to their references) or the title's 19 (a
    layout use left unmapped). Itemised in the local herd record.
  - *Component dimensions are not spacing* and stay component constants: the avatars (80 / 58px), the card min-heights (348 / 250px), the member cards
    (118 / 85px), the Back button (46x51 / 34x36px), the file tree (340 / 354px, joined by the container-keyed ramp below: B2d, accepted), the
    action control height (36px).
  - **Open question, for a separate ruling (not part of this extension):** 16px is used 22 times in the stage and is not a component step. That is
    evidence the COMPONENT scale may lack a step; adding one would touch every surface, not only the stage.
- **The rail** is the only home of + New Orchestrator and Reception in the chrome. The side panel is `DI_PANEL_WIDTH_PX` wide (`webview-ui/src/constants.ts`, 428px today; the constant governs; D0 wrote 420, corrected 2026-11-05). The request workspace docks beside it
  when there is room and otherwise covers the window and says so (`layoutFor` in `diMissionRequestModel.ts`).
- **Shipping sizes** the browser tests use: 1440x900 and 1600x1000, plus the narrow and reflow sizes named in `PRODUCT.md`.
- **Two panes at 1440 and wider (Direction B, Q9; B2b, accepted):** a scan-face screen (Mission details, Code Diff, Comments, Evidence, a rail page)
  docks between the rail and the Mission list, which stays beside it. Below 1440 the screen covers the list, which stays mounted underneath and keeps
  its tab, search, filter and scroll. One state decides both the screen's edge and whether the list is covered (`DI_SCAN_TWO_PANE_MIN_PX` in
  `webview-ui/src/constants.ts`), and the Escape grammar is the same in both modes. 1280 was measured and does not fit two panes.
- **A container-keyed ramp between two declared widths (Direction B, B2d, accepted; the Lead's ruling, 2026-11-05):** when a component steps between
  two declared widths at a container threshold, and the step would take room from a neighbour that must never shrink as the window grows, it RAMPS
  instead. It holds the smaller width up to the threshold, then grows 1:1 with the CONTAINER to the larger one:
  `clamp(small, calc(small + (100cqw - threshold)), large)`. It is keyed to the container the room belongs to, never to the window. The instance: the
  docked Code Diff's file tree, 340px up to a 1015px stage (F-b's T3 threshold, `@container scanstage`), then 1:1 to 354px at a 1029px stage
  (`diScanPages.css`). A step would take 14px from the code at the threshold; the ramp keeps the code channel from ever shrinking as the window grows
  (measured monotonic across the full width set).
- **Reading cap (Direction B, (a); B2b, accepted):** prose in the scan face's stage is at most about 70ch wide, left-aligned, and the space beyond it
  is left deliberately. Never on tables, code, identifier or hash lists, and never a smaller font instead.
- **Code Diff reading space (Direction B, B2d, accepted; the Supervisor's bar, 2026-11-05):** the persistent Mission list stays beside Code Diff by
  default at shipping sizes. Code keeps its size (`--di-font-code`, 12px); fitting content is a layout or disclosure responsibility, never a smaller font.
  - The file tree has an obvious, labelled, keyboard-reachable and reversible collapse (`Hide files` / `Show files`, with `aria-expanded`), open by default.
    It sits in the editor bar, outside the tree, so collapsing strands no focus and leaves no strip in the code; the control's own space counts in any
    measurement. The choice is remembered for the session, across navigation and resize. Collapsing dismisses no other layer and keeps the Escape grammar.
    `Find a file` searches the tree, so invoking it with the files collapsed OPENS the tree and its box: the control and what it searches are never
    in contradictory states (B2d, round 20).
  - Long lines keep their content and indentation and are reached by horizontal scrolling, by keyboard too. A line past the edge is overflow that
    scrolls, never lost content, and is never silently clipped or truncated, nor drawn in a smaller font.
  - File names, paths and the selection stay discoverable and operable.
  - There is no universal character minimum. A measured sample's fit is a bounded observation (its fixture, face and viewport), never a claim that any
    code fits.
- **New Mission's two panes (Direction B, the selected handoff; B2e, accepted):** Recent is a LEFT pane, 187px, and the
  conversation is docked beside it, taking what remains after the page's 64px padding and a 24px gap (`--di-layout-1`).
  - The conversation is **696px at 1600x1000** (the old single column's width, kept), **536px at 1440x900 (160px LESS than the old 696px column: a
    disclosed cost)**, and 804px at 1280x800 (where the Mission list is covered). These are the widths with NOTHING minimized. A minimized surface puts a chip in the strip, the screen yields the strip's width (44px), and the conversation, which takes the remainder, is narrower by exactly that width: measured with the requests minimized it is 492px at 1440x900 and 652px at 1600x1000 (the B3 allocation test records both against the same page with no chip). At 1280x800 the same rule applies; that figure is not separately measured.
  - Each pane scrolls inside itself, so the title, the conversation's way out ("Close this conversation"), the Recent pane and Send stay on screen while a
    long conversation scrolls. The conversation's own overflow is expected.
  - The pick is DRAWN, with the list's selected treatment (the yellow edge on the selected surface).
  - **The picked ask, clicked again, CLOSES the conversation**, in every state of its read (even one that could not be read), by the same transition as
    "Close this conversation": the blank start, the typed ask kept exactly, focus in the box, nothing read again and nothing sent.
  - **A read that failed is retried by a labelled control of its own, never by re-picking:** "Read this conversation again", in the conversation's pinned
    head row beside "Close this conversation", drawn while the page says the read failed and no read of it is in flight. It moves focus FIRST to "Close
    this conversation" beside it, which stays put while the conversation draws (the box would leave the view under it). A second Enter there closes the
    conversation, as a second Enter on a picked ask deselects it. The not-read notice names the control word for word.
  - Recent's asks are one per row and their words WRAP (the panel caps first words at 120 characters); the type size is unchanged.
  - The newest turn comes into view when a turn ARRIVES: a fresh send, a later turn or draft in the conversation drawn, or the first turn into a
    conversation already drawn as read and empty (a send into it). The FIRST draw of a conversation that was read (a pick, a switch to another, or
    a read that lands after "not read") does not move the page.
  - Where the page itself is the scroller (the stacked fallback), its sticky head's strip is reserved as the stage's scroll-padding-top
    (`diStickyPad.ts`, measured, never a constant), so an arriving turn or a focused control is scrolled clear of the head, never under it. It is
    re-measured when the head resizes AND when the scroller's own children change: the head's sticky offset can move with no change of size (fixture
    mode's label mounting before the page moves it from 0 to 30px; measured at 760x700 stacked, the restart simulated at the message boundary).
  - The DOM order is unchanged (the conversation, then Recent), so Tab order is as it was. The grid DRAWS Recent on the left, so in the split Tab does NOT
    follow the drawn left-to-right order: it starts in the conversation, on the right. A known cost of the allocation, for B3's route audit (1A-R).
  - **While the request workspace holds the ask** (docked over the stage's right, above it by design), the panes SWAP columns: the conversation returns to
    the page's left edge, where "Bring the ask back here" stood before the split, exposed at 1440x900 and 1600x1000. At 1280x800 the workspace covers it,
    as it did before the split.
  - **Focus under the workspace (R22): ISOLATED, never focused unseen.** While the request workspace is shown over the stage (above it by design),
    every stage control in the Tab order that its column covers is `inert` and still drawn, and so is a covered scroll container that would
    otherwise become a Tab stop of its own (`diCoverIsolation.ts`). A covered CONTROL is drawn with the stage's own unavailable treatment (the
    opacity of its disabled controls), so a sliver of it showing past the workspace's edge never looks live while a pointer on it does nothing
    (measured: four such slivers at 1280x800, two at 1440x900, none at 1600x1000). Nothing moves and nothing changes on focus; only marks
    the isolation set are ever cleared. Measured at 1280x800, 1440x900 and 1600x1000: the Tab ring, forward and backward, lands only on controls a
    person can see.
    - The isolated controls' route is to LEAVE the held state by its one labelled, keyboard-reachable exit, which restores every one of them with the
      ask's exact words: "Bring the ask back here" at 1440x900 and 1600x1000, the workspace's own "Close requests" at 1280x800 (where the workspace
      covers bring-back). "Review requests" reopens the workspace as it was.
    - The STATED REASON, in text: while any stage control is isolated, the docked workspace says "The requests cover part of the page. Close the
      requests to use it." It is shown from the isolation's own marks, so it never disagrees with what is covered. The pointer's route is the same
      exit: close the requests, and the ask comes back word for word.
    - Closing RELEASES the isolation first: the workspace returns focus to its opener only after every mark is cleared (`releaseCoverIsolation`),
      so focus never lands on an inert opener (measured: before this, closing onto a covered "Review requests" at 1440x900 left focus on the page).
    - A focused control that BECOMES covered (the window narrows, wider reading widens the workspace) hands focus to the workspace's own heading.
    - The trade, considered: a smaller Tab ring while the workspace covers the stage, and the covered stage is out of the accessibility tree while it
      is shown. That is the standard behaviour behind a covering surface, but the workspace is non-modal, so someone could reasonably expect to read
      behind it: a considered cost.
    - With the workspace closed nothing is isolated: every stop is seen at 1280x800, 1440x900 and 1600x1000.
    - RESTORE (B3): no open screen is ever over the minimized-surface strip. Two panes (1440px and wider): the screen ends where the strip begins,
      and the strip stays beside the list dock. One pane: the screen ends one strip-width short of the window's edge and the strip takes that edge, at
      the screen's layer, above the dock and below the covering workspace. The strip holds the Restore controls of the requests, the office panel,
      Reception and Code Review, so one rule serves all four. Restoring brings back the retained state and puts focus on the control that last held it.
      The request workspace has ONE declared exception to an exact scroll (B3; Supervisor ruling R2, 2026-11-08, retained at `.herd/state/supervisor-restore-focus-ruling-20261108.md`, not committed): the kept scroll is given back first, and the
      view then moves ONLY as far as the control that last held focus needs to be fully visible, and not at all when it already is (the field's own reveal,
      `scrollIntoView({ block: 'nearest' })`). Focus is never left where nobody can see it, and the reading position is not kept at that cost. The restore
      target changed under 1B: "Wider reading" and Minimize are items of the Options menu, which is excluded from last-content tracking, so the control that
      last held focus is the content control the person last worked in, and it can lie outside the kept view. The AC3 request test pins it: the restored
      target is the right element AND is seen; the scroll is judged against an oracle derived from the target's, the scroller's and the window's geometry
      read before the reveal (never from the resulting scroll), with under-scroll, over-scroll and already-visible (nothing moves) controls.
      Where a fixture label is drawn as the panel's strip (a narrow window), the strip starts below it, as the rail and the screen do.
  - **The fallback, bounded:** a stage under 604px (the pane, the gap, the conversation's measured 329px min-content and the page padding) STACKS the
    panes, and the page scrolls as one column; there the pick and the conversation are NOT on screen together. Every existing narrow condition is
    wider (the smallest, 900px wide, is a 700px stage), so the fallback is NOT the general case.
  - **A known non-conformance, NOT closed by B2e:** the picked state draws TWO primaries (the draft's "Open it in the request workspace to submit" and
    Send) against the one-primary rule. B3 corrects it with ONE predicate, `draftIsOpenable`, read by both the thread's open control and Send's rank, so the page never draws both as primaries and never draws neither while a proposal waits to be opened (established in the Chromium primary matrix, below).

## Elevation & Depth

Flat by default. Elevation is chrome only and rare, and none of it is a frontmatter primitive:

- `--di-shadow-pop` for a popover or dialog; `--di-shadow-primary` for the one primary action; `--di-scrim` (a colour, above) behind a dialog.
- `--di-glow` / `--di-glow-peak` mark owner attention and are always paired with a word or glyph.
- No gradients, glass or decorative glows on surfaces.
- **Stacking** is classified in `test/diMissionRequestStacking.test.ts`: dialogs sit above the covering workspace, the workspace above the world's
  overlays.

## Shapes

Radii only from the scale: `di-radius-sm` 6px (buttons), `di-radius` 8px (controls), `di-radius-box` 10px (boxes, draft cards), `di-radius-card`
12px (cards), `di-radius-pill` (pills and chips).

**Conformance today (a non-conformance, NOT a permitted exception):** existing DI chrome does not meet this scale. The same run found **13 radii
off it**: 8 in the scan-face stylesheets and 5 in the code review dialog, the operational panel and the request workspace. One of them, `.di-rv`'s
16px, is asserted by `test/diCodeReviewContrast.test.ts:243`. Each is itemised in the same `DISPOSITIONS.md`. All are **OPEN**, to be resolved by the
direction Travis selects. The rule above stands as written.

## Components

**Direction B, B3 (the behaviour of the applied source; acceptance is CITED here, never claimed: tray and Restore at `.herd/state/b3-tray-lead-acceptance.md`, scoped to its frozen packet `verification/b/b3-review-2/`; the guarded apply of candidate-fix-6 at `.herd/state/b3-apply2-lead-acceptance.md`; the restore-scroll exception at the R2 ruling cited above; all retained in the evidence tree and not committed):** secondaries live in ONE anchored, non-modal popover (`components/ui/AnchoredPopover.tsx`, `components/diPopover.ts`) on three surfaces: the Mission list's **Options** (Settings, and the filter, worded by its criterion, "Needs you only"; the closed trigger reads "Options (needs you only)" while it is on), the request workspace's **Options** (reading width, an action whose label names what it does and carries no `aria-pressed`, and Minimize), and Code Diff's **More** (Find a file, Settings). **Copy the lines shown**, the Files toggle and every Close stay direct. A held menu item draws its reason in the item; the direct Copy draws its reason in the editor bar's result slot while it has focus, and says its outcome (copied, clipboard absent, clipboard refused) in the same slot. Routes, drawn only where they can act and visible when focused: "Go to the Map", "Go to minimized surfaces (n)", "Back to the Mission list" (it exposes the list: a minimized office is expanded first, and at one pane with a Mission open it activates Home; the list's scroll is kept) and "Skip the file list". "Wrap lines" is an optional soft wrap, a display preference, off by default, kept for the life of the panel. A refused read of the picked request says so and offers "Read it again"; a refused Code Diff read offers "Refresh". The proposal's heading takes its level from its caller (3 under New Mission's "This conversation" and under the request workspace's own title, 4 under an open request's title; its proof-rule sub-headings sit one level below it), so the page's heading outline never skips a level, and it is drawn the same at every level. Layers: an anchored popover's own layer is 1 and local; the two popovers inside the scan stage (Code Diff More, Mission details More actions) are local to the stage (position fixed, layer 30), and the Mission list's Options is local to the dock (layer 20, 43 while a screen is open), whose `overflow: hidden` bounds it; the dock is below the workspace beside it (21) and below the covering layer (44), and a new popover on the scan face is classified by name in `test/diMissionRequestStacking.test.ts`, never by a count. New Mission's page line and its "ask is open in the request workspace" note use `text-wrap: pretty`. The reading cap above governs the scan face's stage prose only: the request workspace's Wider reading is an opt-in width and has NO stated line-length cap (a gap recorded here, not filled). The product surfaces carry no design-system advisory from the detector (the configured scan's remaining findings are harness-only, dead or the retained Inter choice: docs/design-workflow.md).

**Which browser established which claim (two matrices, never one):** the PRIMARY matrix is Chromium, the installed Chrome driven by Playwright (Chrome 156.0.8078.81 when B3 was verified): the permanent browser tests, at 1280x800, 1440x900 and 1600x1000 where a test names them. It carries every behaviour claim above unless a sentence says otherwise. The SELECTED BRAVE STATES are a different and smaller thing, and NOT a full matrix: selected fixture states measured in the installed Brave at the two shipping sizes only (1440x900 and 1600x1000), and real-key route cases (Tab, Shift+Tab, Enter, Space and ArrowRight; no programmatic focus; the six control calibrations of the visibility helper run in the page first). Six route cases pass at each size, and the picked request's exact-references case is QUALIFIED: its disclosure is reached, opened and closed by real keys, and the one check it did not meet expected a request-reference pattern that the seeded request did not carry (a property of the seed, recorded as one, not a product finding). Brave establishes the claims it names and nothing else; there is no full-matrix claim for Brave anywhere in this document, and a Chromium result is never cited for Brave nor the reverse. The Brave runs span two builds (155.1.97.56 with engine 155.0.8059.40, then 157.1.99.55 with engine 157.0.8103.39 after the browser updated mid-Mission); every cell names its run, and no cell is compared across that boundary. The run and image figures are deliberately NOT stated here, because they are volatile and would rot at the next capture: they live in the final adoption ledger, `.herd/state/verification/brave/FINAL-EVIDENCE-ADOPTION.json` (retained, not committed).

Accepted structure at HEAD `5a1585b`:

- `components/DiScanPanel.tsx`: the scan face, its rail, the view ladder and its Escape ladder.
- `components/DiScanPages.tsx`: Missions, Reception and New Mission (one composer; the thread in place; Recent).
- `components/DiOfficePanel.tsx`: the office panel composition, the inspector dialog, and the minimized tray.
- `components/DiMissionRequest.tsx`: the request workspace (Ask and Requests).
- `components/DiCodeReview.tsx` (live code review dialog) and `components/DiReceptionInbox.tsx` (Reception inbox dialog).
- Buttons: `.di-page-button` (secondary, frontmatter `button-secondary`) and `.di-page-primary` (the one primary, `button-primary`). Action rows
  `.di-new-thread-actions` / `.di-new-draft-actions`, chips `.di-chip`, notes `.di-page-note`. Reuse them before writing new CSS.
- **Shared mechanics, reuse and never reinvent:** `components/diModal.ts` (`useDiModal`: focus-in, Tab trap, Escape, a capture-phase key guard gated
  on `isForemost`); `components/diModalStack.ts` (the one owner of `#root` `inert`, `claimFront` / `releaseFront` / `isForemost`, close-time
  `returnFocusOnClose` with `CLOSE_FOCUS_FALLBACKS`); `components/diMinimize.ts` (`canTakeFocus`, keep and restore a minimized view and focus).
  A front claim is **modal** (the default: `#root` is inert while it is in front) or **non-modal** (`claimFront(owner, { modal: false })`: in
  front, it owns Escape while foremost, and it inerts nothing) (B1, accepted).

**D1 changes (accepted AS OF D1's boundary v2, its tracked patch sha256 `0fce9e26…`, round 7; each item below is present in those bytes; later
stages, B1 to K1, have moved some of these files since, so this names the accepted bytes, not the live tree):** "Close this conversation" in each of
New Mission's four pick states (read with turns, read and empty, still being read, not read: enumerated and asserted by D1's accepted browser test,
`diScanNewMissionDismissBrowser.test.ts` sha256 `48248130…`, `PICK_STATES`, in Chrome; rendered in Brave for ONE picked Recent conversation only,
`.herd/state/verification/brave/brave-manifest-v2.json` steps v2-4 and v2-5, on the D1 candidate tree before boundary v2 was frozen) and New
Mission's own Escape step; the identity-aware late turn reply; the request workspace peeling armed Reject/Withdraw, a
revision, the Ask conversation and the picked request one layer per Escape, with typed words kept per request; `components/ui/Modal.tsx` portalled
outside `#root` through `useDiModal`; the scan rail Home added last to `CLOSE_FOCUS_FALLBACKS`.

## Do's and Don'ts

**Enforced today (exact checks):**

- Don't write a colour literal in TS/TSX: `pixel-agents/no-inline-colors` (ESLint, error; string literals in TS/TSX only, tests included). It does
  not read CSS.
- Don't write an inline `boxShadow` other than `var(--pixel-shadow)` or a hard `2px 2px 0px`: `pixel-agents/pixel-shadow` (ESLint, error).
- Don't write an inline `fontFamily` without FS Pixel Sans: `pixel-agents/pixel-font` (ESLint, error; inline TS/TSX styles only, so DI chrome's
  CSS face is not read by it).
- Do keep every consumed token resolvable where it is used: `test/diTokenScope.ts` (`tokenScopeAudit`, measured in the browser, and only for the
  rendered states of the tests that call it; not a universal CSS check).
- Do style DI chrome stylesheets with tokens only. Two **static** source checks (they read the files; no browser) cover CSS:
  `test/diChromeColourLiterals.test.ts` reads **four** stylesheets (`diOperationalPanel.css`, `diMissionRequest.css`, `diCodeReview.css`,
  `diScanPanel.css`) and fails on a raw colour, allowing only the review dialog's named content-palette properties; and
  `test/diScanPagesContrast.test.ts` reads `diScanPages.css` and fails on any colour written as a literal (besides its contrast pairs).
  `index.css` (the token definitions, the pixel world and the `.di-world-home` block) is scanned by **neither**.
- Do define chrome tokens once, solid, on `:root`: `test/diChromeTokens.test.ts` and its reader `test/diChromeTokens.ts`.
- Do keep the Home token set (`#0d0f1b`, `#552df0`, `#e6e2d6`, `#8b919e`, Inter): `test/diHomeTokens.test.ts`.
- Do meet the listed contrast pairs: `test/*Contrast.test.ts` (token-pair ratios: 4.5:1 text; 3:1 rings, edges, state marks).
- Do classify every z-index: `test/diMissionRequestStacking.test.ts`.

**Required bar (reviewers apply these; not all are machine-checked):**

- Do keep exactly one primary action per view **and state**; hold unavailable actions with a stated reason (`aria-disabled`); never paint a held
  action as available.
- Do give every picked or opened state a labelled way out. Escape dismisses **at most one layer per press**, and the layer is chosen by **who owns
  the key, not by what is drawn highest** (K1, accepted by the Lead, scoped to its frozen packets): a dialog or popover in front owns it; otherwise the visible surface holding focus (Home,
  a panel, the request workspace) follows its own grammar; with focus fallen to the page body, a surface that currently covers the Map owns it.
  Escape closes the Map with focus on the Map or its own controls, or with focus fallen to the page body while nothing covers the Map; in both,
  nothing is in front of it, and a close from the body gives focus to the Map's toggle (or the world's disclosure, when the toggle is not drawn).
  At a surface's root, with nothing to dismiss, Escape is a **no-op**: no global fallback closes a layer the person is not using. Hidden,
  minimized or unmounted content owns **no** keys (read at the key with `checkVisibility`, never from a state that outlives what it describes).
  Focus never lands on the page body.
- Do let a menu or popover own Escape while open without making the app inert: use the anchored menu (`components/ui/AnchoredPopover.tsx`,
  `components/diPopover.ts`), a non-modal front claim that owns Escape and inerts nothing (B2c, accepted; the claim itself is B1's).
- Do assert a menu by its DRAWN GEOMETRY, never by its presence: each item is SEEN and hit-testable at its own centre, its label is as wide as its own text, and a click at the item's centre activates that item (B3: the first browser suite asserted the items by DOM and the popover's role while the Code Diff More menu was 14px wide at 1440x900 and 1600x1000).
- Do make every clipping rule follow the CONTAINING BLOCK (B3, a named trap; THREE tools made the same mistake): an ancestor's `overflow` does not clip a `position: fixed` descendant unless the ancestor establishes a containing block for fixed descendants. The popover's frame walk, an earlier visibility helper and the detector's "positioned child clipped by overflow container" rule each treated every overflow ancestor as clipping and reported a fixed child as hidden (at the detector's flagged target, the Code Diff More menu lies 255.5px outside the flagged aside's box and every item hit-tests, because its containing-block chain runs through the fixed stage and not through the aside). THE 29-CASE CONTAINMENT TABLE below is an OBSERVATION of two builds, Chrome 156.0.8078.81 and Brave's engine 155.0.8059.40, taken before the installed Brave updated to engine 157.0.8103.39, where it was NOT re-measured: it is not a claim about any other build and not a standard. Trigger list (a position:fixed descendant contained by an ancestor carrying it; observed outcomes identical in the two builds): transform, translate, rotate, scale, perspective, filter, backdrop-filter, contain paint | layout | strict | content, will-change transform | perspective | filter, transform-style preserve-3d, content-visibility auto | hidden. Observed NOT to contain, in both: contain size | style, will-change opacity | top, container-type size | inline-size, isolation, opacity, mix-blend-mode, clip-path (which still clips a pointer on the fixed child), overflow clip. 28 of 29 rows matched the expectation in each build; the one that did not (content-visibility: hidden, expected not to contain) is kept as measured.
- Do give a control that REMOVES ITSELF a surviving, seen destination BEFORE it goes, synchronously (B3, measured): Reception's Clear info and the New Mission outcome's Dismiss dropped focus to the page body until B3, while the request workspace's notice Dismiss landed on the workspace heading because that surface has a FOCUS KEEPER (a MutationObserver on its root that focuses its fallback heading when the focused element is gone and focus has fallen to the body; with the keeper disabled the same control lands on the body: measured). The two repairs act proactively where the destination is known. LATER HERD'S WORK, not B3's: a keeper is the better GENERAL answer because it catches removals nobody anticipated, so extend the pattern to every surface that can remove the focused control.
- Don't add tokens, gradients, glass or radii off the scale.

**Detector note:** Impeccable's detector looks for DESIGN.md by walking up from each scanned file and stops at the first directory with a project
marker (`.git`, `package.json` or `.impeccable`) and no DESIGN.md. For files under `integrations/pixel-agents/webview-ui/` that is
`webview-ui/package.json`, so this root file alone is **not** applied to them. `integrations/pixel-agents/webview-ui/DESIGN.md` is therefore a regular,
byte-identical copy of this canonical file, guarded by `webview-ui/test/designMdDrift.test.ts`: edit this file, then copy it there. See
`docs/design-workflow.md`.
