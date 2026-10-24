/**
 * Item 4 (original 19): "clear, readable" as arithmetic. Every colour of the Mission request workspace
 * is a SOLID token on `.di-mr` in `diMissionRequest.css`; this test parses that very file and checks every
 * text-on-background pairing the component produces against WCAG 2.1: 4.5:1 for text (AA), 3:1 for the focus
 * ring and for the borders that mark a boundary. Change a token and this checks it.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import {
  ALPHA,
  resolveShared,
  sharedTokensAliasedBy,
  sharedValue,
  SOLID,
} from './diChromeTokens.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** The stylesheet as written: its palette names the shared DI chrome tokens (index.css :root). */
const written = fs.readFileSync(path.join(root, 'src/components/diMissionRequest.css'), 'utf-8');
/**
 * The stylesheet with every `var(--di-*)` resolved through the ONE shared reader (diChromeTokens.ts), so every
 * reader and pin below runs on the values the browser uses. Resolution throws on any token it cannot resolve.
 */
const css = resolveShared(written);

/** The `--mr-*` tokens of the first `.di-mr { ... }` rule, solid `#rrggbb` only. */
function tokens(): Record<string, string> {
  const rule = /\n\.di-mr \{([\s\S]*?)\n\}/.exec(css);
  if (!rule) throw new Error('the .di-mr rule was not found');
  const found: Record<string, string> = {};
  for (const m of rule[1].matchAll(/--mr-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g))
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

const SURFACES = [
  'bg',
  'surface',
  'surface-2',
  'code-bg',
  'info-bg',
  'warn-bg',
  'block-bg',
  'ok-bg',
  'danger-bg',
  'tab-on-bg',
];

describe('the stylesheet’s colour tokens are solid and complete', () => {
  it('every token the component relies on is defined as #rrggbb', () => {
    for (const name of [
      'bg',
      'surface',
      'surface-2',
      'code-bg',
      'border',
      'border-soft',
      'text',
      'muted',
      'link',
      'focus',
      'btn-bg',
      'btn-hover',
      'btn-text',
      'primary-bg',
      'primary-text',
      'danger-bg',
      'danger-border',
      'tab-on-bg',
      'selected-edge',
      'info-bg',
      'warn-bg',
      'block-bg',
      'ok-bg',
      'step-done',
      'step-now',
      'step-next',
      'scroll-thumb',
      'scroll-track',
    ]) {
      expect(T[name], name).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it('the stylesheet draws no colour outside those tokens (a literal here would escape this check)', () => {
    // Hard-offset `box-shadow` decorations carry no text and are the pane's own edge; everything else is a token.
    const outside = css
      .replace(/\n\.di-mr \{[\s\S]*?\n\}/, '')
      .replace(/box-shadow:[^;]*;/g, '')
      .match(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/g);
    expect(outside ?? []).toEqual([]);
  });

  it('every shared token the palette resolves through is solid in index.css :root (the check follows the tokens there)', () => {
    const aliased = sharedTokensAliasedBy(written, '.di-mr');
    expect(aliased.length, 'the palette names the shared tokens').toBeGreaterThan(10);
    for (const name of aliased) {
      const value = sharedValue(name);
      expect(value, `--di-${name} carries no alpha colour`).not.toMatch(ALPHA);
      if (value.startsWith('#')) expect(value, `--di-${name}`).toMatch(SOLID);
    }
    // Every colour token this file checks is a solid colour at its source, not only once resolved here.
    const colourAliases = [
      ...written.matchAll(/--mr-([a-z0-9-]+):\s*var\(--di-([a-z0-9-]+)\)\s*;/g),
    ].filter((m) => m[1] in T);
    expect(colourAliases.length).toBe(Object.keys(T).length);
    for (const [, mr, di] of colourAliases) expect(sharedValue(di), `--mr-${mr} -> --di-${di}`).toMatch(SOLID);
  });
});

describe('text is readable on every surface it appears on (WCAG AA, 4.5:1)', () => {
  it('primary text on every surface', () => {
    for (const surface of SURFACES) {
      expect(ratio('text', surface), `text on ${surface}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('muted text on the surfaces it is used on', () => {
    for (const surface of ['bg', 'surface', 'surface-2', 'code-bg', 'info-bg', 'warn-bg']) {
      expect(ratio('muted', surface), `muted on ${surface}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('links on the surfaces they are used on', () => {
    for (const surface of ['bg', 'surface', 'surface-2']) {
      expect(ratio('link', surface), `link on ${surface}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('button text on every button state, and the primary action', () => {
    expect(ratio('btn-text', 'btn-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('btn-text', 'btn-hover')).toBeGreaterThanOrEqual(AA);
    expect(ratio('primary-text', 'primary-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('text', 'danger-bg')).toBeGreaterThanOrEqual(AA);
  });
  it('the journey: done, now and next, on the surfaces the steps sit on', () => {
    for (const name of ['step-done', 'step-now', 'step-next']) {
      for (const surface of ['bg', 'surface', 'surface-2']) {
        expect(ratio(name, surface), `${name} on ${surface}`).toBeGreaterThanOrEqual(AA);
      }
    }
  });
});

describe('boundaries and focus are visible (3:1)', () => {
  it('focus is unmistakable on every surface it can be drawn against', () => {
    // The ring is an OUTLINE drawn 2px OUTSIDE the control (`outline-offset: 2px`), so what it must stand
    // out from is what lies behind the control (a pane surface, or a neighbouring button), never the control's
    // own fill: the primary button is the same yellow as the ring, and the ring still shows around it.
    for (const surface of [...SURFACES, 'btn-bg', 'btn-hover']) {
      expect(ratio('focus', surface), `focus on ${surface}`).toBeGreaterThanOrEqual(BOUNDARY);
    }
    expect(css).toMatch(
      /:focus-visible \{\s*outline: 3px solid var\(--mr-focus\);\s*outline-offset: 2px;/,
    );
  });
  it('the strong border and the danger border stand out from the surfaces they edge', () => {
    for (const surface of ['bg', 'surface', 'surface-2']) {
      expect(ratio('border', surface), `border on ${surface}`).toBeGreaterThanOrEqual(BOUNDARY);
    }
    expect(ratio('danger-border', 'danger-bg')).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the selected tab, the selected row and the primary action edge stand out from the surfaces behind them', () => {
    for (const surface of ['bg', 'surface', 'surface-2']) {
      expect(ratio('selected-edge', surface), `selected-edge on ${surface}`).toBeGreaterThanOrEqual(BOUNDARY);
    }
  });
  it('the always-drawn scrollbar thumb stands out from its track', () => {
    expect(ratio('scroll-thumb', 'scroll-track')).toBeGreaterThanOrEqual(BOUNDARY);
  });
});
