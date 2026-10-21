/**
 * feature13: "readable" as arithmetic, for the calm operational panel and its inspector. Every colour
 * is a SOLID token on `.di-op-theme` in `diOperationalPanel.css`; this test parses that very file and
 * checks every text-on-surface pairing the components can produce against WCAG 2.1 — 4.5:1 for text
 * (AA), 3:1 for the focus ring and the edges that mark a control. Change a token and this checks it.
 * Mirrors diCodeReviewContrast.test.ts, for the surface that replaced the sidebar's pixel styling.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const css = fs.readFileSync(path.join(root, 'src/components/diOperationalPanel.css'), 'utf-8');
/** The stylesheet without its comments (what the browser acts on). */
const code = css.replace(/\/\*[\s\S]*?\*\//g, '');

/** The `--op-*` tokens of the first `.di-op-theme { ... }` rule, solid `#rrggbb` only. */
function tokens(): Record<string, string> {
  const rule = /\n\.di-op-theme \{([\s\S]*?)\n\}/.exec(css);
  if (!rule) throw new Error('the .di-op-theme rule was not found');
  const found: Record<string, string> = {};
  for (const m of rule[1].matchAll(/--op-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g))
    found[m[1]] = m[2];
  return found;
}
const T = tokens();

const AA = 4.5;
const BOUNDARY = 3;

function ratio(foreground: string, background: string): number {
  const fg = T[foreground];
  const bg = T[background];
  if (fg === undefined || bg === undefined)
    throw new Error(`no token ${foreground} / ${background}`);
  return contrastRatio(fg, bg);
}

/** The surfaces text sits on: the panel and dock (bg), a card (surface), a control (surface-2), a selected card. */
const SURFACES = ['bg', 'surface', 'surface-2', 'selected-bg'];
const STATUS = [
  'running',
  'waiting',
  'blocked',
  'failed',
  'ended',
  'disconnected',
  'stale',
  'unknown',
  'warn',
  'good',
];

describe('the colour tokens are solid and complete', () => {
  it('every token the components rely on is defined as #rrggbb', () => {
    for (const name of [
      'bg',
      'surface',
      'surface-2',
      'line',
      'edge',
      'text',
      'muted',
      'link',
      'focus',
      'selected-bg',
      'selected-edge',
      ...STATUS,
    ])
      expect(T[name], name).toMatch(/^#[0-9a-fA-F]{6}$/);
  });
  it('no token is an alpha colour a test could not check', () => {
    const rule = /\n\.di-op-theme \{([\s\S]*?)\n\}/.exec(css)![1];
    expect(rule).not.toMatch(/--op-[a-z0-9-]+:\s*(rgba?|hsla?)\(/);
    expect(rule).not.toMatch(/--op-[a-z0-9-]+:\s*#[0-9a-fA-F]{8}\b/);
  });
});

describe('text is readable on every surface it appears on (WCAG AA, 4.5:1)', () => {
  it('primary text on every surface', () => {
    for (const s of SURFACES) expect(ratio('text', s), `text on ${s}`).toBeGreaterThanOrEqual(AA);
  });
  it('muted text on every surface it is used on', () => {
    for (const s of SURFACES) expect(ratio('muted', s), `muted on ${s}`).toBeGreaterThanOrEqual(AA);
  });
  it('links on every surface', () => {
    for (const s of SURFACES) expect(ratio('link', s), `link on ${s}`).toBeGreaterThanOrEqual(AA);
  });
  it('every status colour (words, shapes, cautions) on the card, panel, dock and selected card', () => {
    for (const status of STATUS)
      for (const s of SURFACES)
        expect(ratio(status, s), `${status} on ${s}`).toBeGreaterThanOrEqual(AA);
  });
  it('the inspector reads on the same bg/surface pairs (it reuses the panel tokens)', () => {
    expect(ratio('text', 'bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('muted', 'surface')).toBeGreaterThanOrEqual(AA);
  });
});

describe('status is legible beyond colour: each status has its own colour AND the shared tokens are distinct enough to tell apart in words', () => {
  it('the eight statuses use a set of tokens (shapes and words are asserted in diOperationalStatus.test.ts)', () => {
    const eight = [
      'running',
      'waiting',
      'blocked',
      'failed',
      'ended',
      'disconnected',
      'stale',
      'unknown',
    ];
    for (const s of eight)
      expect(css).toMatch(new RegExp(`\\.di-op-status\\[data-status='${s}'\\]`));
  });
});

describe('the focus ring and the control edges are visible (3:1)', () => {
  it('the focus ring against every surface a focused control sits on', () => {
    for (const s of SURFACES)
      expect(ratio('focus', s), `focus on ${s}`).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the edge of a button, input or select against the surface behind it', () => {
    // Controls sit on the panel (bg) or on a card (surface); inputs and selects use `surface` fill.
    expect(ratio('edge', 'bg')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('edge', 'surface')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('edge', 'selected-bg')).toBeGreaterThanOrEqual(BOUNDARY);
    // ...and against the control's own fill (a button is drawn on surface-2).
    expect(ratio('edge', 'surface-2')).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the edge of a primary (selected-tinted) button against the card it sits on', () => {
    expect(ratio('selected-edge', 'surface')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('selected-edge', 'selected-bg')).toBeGreaterThanOrEqual(BOUNDARY);
  });
});

describe('the stylesheet keeps its promises', () => {
  it('draws a focus ring on every focusable control and removes the default outline only to replace it', () => {
    expect(code).toMatch(
      /\.di-op-theme :focus-visible \{[^}]*outline: 2px solid var\(--op-focus\)/,
    );
    // The ONLY outline removals: `.di-op-theme :focus` (immediately replaced by the rule above) and
    // the dialog container, a programmatic focus target (role=dialog, tabindex=-1), not a control.
    const removals = [...code.matchAll(/([^{}]+)\{[^}]*outline:\s*(?:none|0)\s*;/g)].map((m) =>
      m[1].trim(),
    );
    expect(removals.sort()).toEqual(['.di-op-inspector:focus', '.di-op-theme :focus']);
    expect(code).toMatch(
      /\.di-op-theme :focus \{\s*outline: none;\s*\}\s*\.di-op-theme :focus-visible \{/,
    );
  });
  it('never animates by itself: the only transition sits inside prefers-reduced-motion: no-preference', () => {
    const gated = /@media \(prefers-reduced-motion: no-preference\) \{[\s\S]*?\n\}/.exec(code);
    expect(gated, 'the gated block').not.toBeNull();
    const outside = code.replace(gated![0], '');
    expect(outside).not.toMatch(/\btransition\s*:/);
    expect(outside).not.toMatch(/\banimation(-[a-z]+)?\s*:/);
    expect(outside).not.toContain('@keyframes');
  });
  it('uses the reading faces: a system sans for text and a monospace for identifiers, never the pixel font', () => {
    expect(css).toContain('--op-font-ui: system-ui');
    expect(css).toContain('--op-font-code:');
    expect(css).toContain('ui-monospace');
    expect(css).not.toMatch(/FS Pixel Sans/);
    // index.css sets the pixel face on EVERY element; this subtree takes the reading face back.
    expect(code).toMatch(/\.di-op-theme \*,[\s\S]*?font-family: inherit;/);
  });
  it('does not shrink type to fit: no text smaller than 11px, and the base is 13px', () => {
    const sizes = [...code.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)].map((m) => Number(m[1]));
    expect(sizes.length).toBeGreaterThan(5);
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11);
    expect(/\n\.di-op-theme \{[\s\S]*?font-size: 13px;/.test(css)).toBe(true);
  });
  it('keeps every inspector scroll inside the dialog: it clips, its body scrolls, the page never does', () => {
    expect(code).toMatch(/\.di-op-inspector \{[\s\S]*?overflow: hidden;/);
    expect(code).toMatch(
      /\.di-op-inspector-body \{[\s\S]*?overflow-y: auto;[\s\S]*?overflow-x: hidden;/,
    );
    expect(code).toMatch(/\.di-op-inspector-backdrop \{[\s\S]*?overscroll-behavior: contain;/);
    expect(code).toMatch(/\.di-op-inspector \{[\s\S]*?min-height: 0;/);
  });
  // Source pins of the layout promises. The behaviour they protect is MEASURED in a real browser:
  // diInspector.test.ts "the Missions list keeps a usable height in EVERY state" (panel overflow 0, a whole
  // Mission card, the dock's actions at rest, Find's results reachable) and e2e/di/panelViewports.spec.ts.
  // Migrated in round 1 from "the panel is ONE scrolling region plus a dock that never scrolls out of the
  // viewport": Find's results are a second, BOUNDED region (they were at HEAD too) and the dock now gives way,
  // so "one region" is no longer the invariant; "one PRIMARY region, every other bounded and internal" is.
  it("the Missions list is the panel's one PRIMARY scroll region; every other scroll region (the dock, Find's results) is bounded and internal", () => {
    expect(code).toMatch(/\.di-op-main \{[\s\S]*?flex: 1 1 0;[\s\S]*?overflow-y: auto;/);
    // the dock scrolls inside its own share (never growing past it)...
    expect(code).toMatch(/\.di-op-selected \{[\s\S]*?max-height: 40%;[\s\S]*?overflow-y: auto;/);
    // ...and Find's results inside a bounded list
    expect(code).toMatch(
      /\.di-op-find-results \{[\s\S]*?max-height: 220px;[\s\S]*?overflow-y: auto;/,
    );
  });
  it('the dock never scrolls out of the viewport: it may give way, but never below its pinned actions', () => {
    // was `flex-shrink: 0` (the dock never gave way). It now shrinks under pressure so the Missions keep their
    // reserved card; the floor holds its header, identity and ALL its actions (they come first in the dock).
    expect(code).toMatch(/\.di-op-selected \{[\s\S]*?flex-shrink: 1;[\s\S]*?max-height: 40%;/);
    expect(code).toMatch(/\.di-op-selected-actions \{\s*min-height: 190px;/);
  });
  it('reserves a usable Missions viewport in every state, and lets Find and the dock give way instead', () => {
    // nothing selected: the heading, the coverage line and one whole card, clear of the fade
    expect(code).toMatch(/\.di-op-main \{[\s\S]*?min-height: 216px;/);
    // a selection: one whole card, clear of the fade (its Mission is kept at the top of the list)
    expect(code).toMatch(/\.di-op-has-selection \.di-op-main \{\s*min-height: 136px;/);
    // Find may shrink; its results scroll, bounded above (220px) and below (one whole result, 62px)
    expect(code).toMatch(/\.di-op-find \{[\s\S]*?flex: 0 1 auto;[\s\S]*?min-height: 0;/);
    expect(code).toMatch(
      /\.di-op-find-results \{[\s\S]*?max-height: 220px;[\s\S]*?min-height: 62px;[\s\S]*?overflow-y: auto;/,
    );
    // a short window: spacing and two pieces of descriptive text give way before anything shrinks or hides a doorway
    expect(code).toMatch(
      /@media \(max-height: 820px\) \{[\s\S]*?\.di-op \{\s*gap: 10px;[\s\S]*?\.di-op-entry-tagline \{\s*display: none;[\s\S]*?\.di-op-review-hint \{\s*display: none;/,
    );
  });
  it('lays long identifiers out so they wrap inside their row (never widening the page)', () => {
    expect(code).toMatch(/\.di-op-row > :last-child \{[\s\S]*?overflow-wrap: anywhere;/);
    expect(code).toMatch(/\.di-op-inspector-title \{[\s\S]*?overflow-wrap: anywhere;/);
  });
});
