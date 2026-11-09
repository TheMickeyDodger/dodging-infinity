# PRODUCT.md — DI World (the Pixel Agents DI office, browser)

Durable product context for design and engineering work on the DI office's web UI. The canonical vocabulary is
`integrations/pixel-agents/CONTEXT.md` (glossary) and `docs/pixel-agents-office.md` (operator reference); where this file and those disagree,
they win. Visual rules and the design workflow are in `DESIGN.md`.

This file separates two things on purpose: **the required bar** (what every new or changed surface must meet) and **what is verified today**
(the exact checks that exist, and what they do not cover). A requirement is not a claim that the whole product already meets it.

## Platform

web

A browser package only, served by the office's own local server (`node dist/di-cli.js`) and rendered with React 19 under
`integrations/pixel-agents/webview-ui/`. There is no native wrapper. The Free desktop app and the Electron shell are out of scope for this UI work
(the approved Mission excludes them; its plan reference, §9.1 2B, is not in this repository).

## Users

- **The operator**: one person on their own machine who asks for engineering work, decides on exact proposals, starts them, and watches Missions
  progress. They own every decision and every delivery step. The office never acts as them.
- **Contributors and later herds** working on this UI, who inherit this file and `DESIGN.md` as instructions.

## Product Purpose

DI World is a pixel-art office in a browser that **shows** what Dodging Infinity reports about Missions, their roles and their task chains, and gives
the operator a few deliberate, bounded controls. The scan face (the side panel with the "DI World" rail) is where the operator works:

- **Home**: the Missions list (Open, Merged, Closed, Archived) beside the living office world.
- **Mission details**: one Mission's state, Swarm, Delivery, Code Diff, Comments, and Evidence and review.
- **Reception**: what needs the operator now.
- **New Mission** (rail **+ New Orchestrator**, Cmd N): one screen, one box. The box is the request workspace's Ask draft. **Send** is one read-only
  drafting turn of the **Codex Orchestrator**. A drafted proposal appears in place and is said to be a draft.
- **The request workspace** (Ask and Requests): where a draft is submitted as a proposal of record, decided on, and started.
- **Herdr**: opens the Mission's herd workspace in a terminal, typing nothing into it.

It observes and does not control, with three opt-in exceptions, each a process the operator starts: the Codex Orchestrator request panel, the
office delivery adapter (one confirmed commit, push or pull request at a time, under the delivery's own authorization), and the Herdr terminal.
It carries **no delivery authority** of its own. It never merges, tags, releases or deploys, and a verified result is not a delivered one.

**Mode: Operate.** In Impeccable's terms (`reference/mode-operate.md` of the pinned skill, see `DESIGN.md`), these surfaces are *Operate*: the
operator completes a task, so scanability, consistency, native expectations and the real usage scene outrank expression, and brand lives in precise
details. The approved Mission's phrase "calm Operate-mode craft" refers to this. Its other phrase, "DI World Free web UI", names the browser office
described above; "Free" is not defined anywhere else in this repository.

## Principles (the required bar)

Every new or changed surface must meet these. Review applies them; not all are machine-checked (see "Verified today").

1. **Say what is true, plainly.** An unread list is said to be unread, never "none". An unknown is never shown as spare capacity. A failed read says
   "Not read", with the reason. A fixture is labelled as a fixture on every screen.
2. **Words carry no authority they do not have.** The assistant only drafts. A draft is not a request. Submitting, approving and pressing **Start**
   are separate steps in the request workspace, and nothing on New Mission performs them.
3. **One composer, one draft.** An ask is typed once. Two editable boxes are never drawn for the same draft.
4. **Never discard what the operator typed.** Navigation, picks, failed Sends, closing a workspace or dialog, and Escape keep unsent words. A draft
   typed for one request never follows to another. The one control that clears the box on purpose is the thread's explicit **Start a new
   conversation**.
5. **No dead ends.** Every picked or opened state has a labelled way out. Escape peels **at most one layer per press**, in the surface that owns
   the key (K1, accepted by the Lead, scoped to its frozen packets): an open dialog or menu first; otherwise the surface the operator is using, where focus is, from its nested pick (a
   conversation, a request, an armed Reject or Withdraw, a revision) to its screen, then Home. The Map closes on Escape only while it is the surface
   the operator is using, and focus then lands on an intentional, visible control. WHEN the Map owns the key and WHERE focus goes are DESIGN.md's
   Escape rule (Do's and Don'ts, the labelled-way-out rule), referenced here and not restated, so they can only change in one place. With nothing
   left to dismiss, Escape does nothing: it never closes a surface the operator is not using, and a hidden or minimized surface never acts on a key.
6. **Focus is always somewhere deliberate.** It never lands on the page body. When the control that had focus disappears, focus moves first to the
   place that keeps the operator's work (the box), or to the opener, or to a named fallback.
7. **One primary action per view and state**, with unavailable actions drawn held, with their reason, rather than hidden or painted as available.
8. **Colour never carries meaning alone.** Every status is a word plus a shape plus a colour.
9. **Code is read at its own size.** How Code Diff makes room for code (the list beside it, the file tree's collapse, long lines that scroll) is
   DESIGN.md's Code Diff reading-space rule, referenced here and not restated.
10. **A pick is drawn, and undone where it was made.** How New Mission keeps Recent beside the conversation (the pane, the drawn pick, the pick clicked
    again closing it, a failed read retried by its own labelled control and never by re-picking, the panes' swap while the request workspace holds the
    ask, and what stays on screen while a conversation scrolls) is DESIGN.md's New Mission two-pane rule, referenced here and not restated.

## Accessibility & Inclusion

**Required bar:** WCAG 2.2 AA for every DI surface, keyboard-complete operation, and the principles above.

**Verified today (automated and partial; this is NOT whole-product conformance):**

- **Contrast, as token-pair ratios** computed from the `:root` tokens through the shared reader `test/diChromeTokens.ts` (not rendered-pixel
  measurement): `diScanPanelContrast`, `diScanPagesContrast`, `diMissionRequestContrast`, `diOperationalPanelContrast` and `diCodeReviewContrast`
  (`*.test.ts`) check the listed text pairs at 4.5:1 and focus rings, control edges and state marks at 3:1. Pairs not listed there are not checked.
- **Reflow (SC 1.4.10)** at the sizes named in `diPanelLayout`, `diPanelKeyboard`, `diMissionRequest` and `diCodeReview` (`*.test.ts`, helper
  `test/diReflow.ts`). **Focus not obscured (SC 2.4.11)** through `test/diTabTraversal.ts` where those tests use it. **Non-text contrast (SC 1.4.11)**
  in `diHomeConvergence.test.ts`.
- **Keyboard paths and focus return** for the flows those browser tests drive (`diPanelKeyboard`, `diScanNewMissionStatesBrowser`,
  `diScanRequestCloseBrowser`, `diCodeReview`, `diInspector`, `diReceptionInbox`, and others). Flows no test drives are not verified.
- **Reduced motion**: `prefers-reduced-motion` rules exist in `diMissionRequest.css`, `diCodeReview.css` and `diOperationalPanel.css`. Browser tests
  emulate reduced motion to run deterministically; that is not a check that every animation honours it.
- **Not verified by anything here:** screen-reader output, Brave rendering, and every DI surface at every shipping size. The D1 changes below are accepted
  AS OF their boundary, not in the live tree, and their Brave rendering is narrower still (one picked Recent conversation, on a candidate tree with no
  recorded identity: DESIGN.md says where).

**D1 (accepted AS OF D1's boundary v2, its tracked patch sha256 `0fce9e26…`, round 7; each item is present in those bytes; later stages have moved
some of these files since):** the New Mission dismissal and Escape layers, the identity-aware late reply, Settings and the shared modal owning their
keys, and the request workspace's one-layer Escape, with their own browser tests (`diScanNewMissionDismissBrowser.test.ts`,
`diEscapeLayersBrowser.test.ts`).

## Terminology

Use `CONTEXT.md` exactly. The terms this UI work touches most:

- **Mission**, **Codex Orchestrator** (the request assistant, never "chatbot"), **Draft** (not "proposal"), **Proposal of record**, **Offer**,
  **Attested approval**, **Withdrawal**, **Start** (not "run" or "dispatch"), **Hold**, **Fixture preview** (not "demo" or "mock").
- **Agent**, **Character**, **Lead**, **Teammate**, **Sub-agent** (pixel-office terms). **Dismiss** means removing an *agent* from the office. Leaving a
  picked conversation is **Close this conversation**, not "dismiss".
- **Select** marks a character. **Focus** is reserved for terminals and for keyboard focus, never the in-office highlight.

## Constraints

- **No delivery from the UI** beyond the one adapter-confirmed step. Delivery authority stays with the operator.
- **Layering**: `core/` depends on nothing; `webview-ui/` depends only on `core/`. `core/src/messages.ts` is generated from `core/asyncapi.yaml`;
  never edit it by hand (CI fails on drift).
- **Tokens only**: no new colour tokens and no raw colour literals in chrome (see `DESIGN.md`).
- **Upstream-shared components** (`components/ui/*`, the pixel office) are also used outside DI. Change them only with the whole caller set reviewed.
