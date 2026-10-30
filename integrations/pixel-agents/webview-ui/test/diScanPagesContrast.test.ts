/**
 * AC-UX-09 for the pass B pages (src/components/diScanPages.css: Missions, Reception, New Mission, the proposal, and a
 * Mission's sibling switch, file tree and Comments), as arithmetic. WCAG 2.2 AA: 4.5:1 for text; 3:1 for a control's edge,
 * a focus ring and a mark that carries state. The thresholds are WCAG's; none is lowered to fit a token.
 *
 * Colours are shared `--di-*` tokens from index.css, read through the ONE shared reader (diChromeTokens.ts). The pairs
 * below are the ones the stylesheet paints; a background the stylesheet starts using that is not reviewed here fails
 * the "every background is a reviewed surface" check, so a new pairing cannot slip in unexamined.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import { resolveShared, sharedValue, SOLID } from './diChromeTokens.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const raw = fs
  .readFileSync(path.join(root, 'src/components/diScanPages.css'), 'utf-8')
  .replace(/\/\*[\s\S]*?\*\//g, '');
/** Throws on any token it cannot resolve (so a missing token fails here). */
const resolved = resolveShared(raw);

const AA = 4.5;
const BOUNDARY = 3;
const c = (name: string): string => {
  const value = sharedValue(name);
  expect(value, `--di-${name} is a solid colour`).toMatch(SOLID);
  return value;
};
const ratio = (fg: string, bg: string) => contrastRatio(c(fg), c(bg));

/** Every declaration as written: [selector, property, value]. */
const decls: Array<[string, string, string]> = [...raw.matchAll(/([^{}]+)\{([^}]*)\}/g)].flatMap((m) =>
  m[2]
    .split(';')
    .map((d) => d.trim())
    .filter((d) => d.includes(':'))
    .map((d): [string, string, string] => {
      const i = d.indexOf(':');
      return [m[1].trim(), d.slice(0, i).trim(), d.slice(i + 1).trim()];
    }),
);
const tokenOf = (value: string) => /^var\(--di-([a-z0-9-]+)\)$/.exec(value)?.[1] ?? null;
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(raw);
  if (!m) throw new Error(`no rule ${selector} in diScanPages.css`);
  return m[1];
}

/** The surfaces the pages paint text on (the stage, a card or well, a raised control, a selected fill). */
const PAGE_SURFACES = ['scan-canvas', 'scan-well', 'scan-raised'];
/** The backgrounds this stylesheet may use, each reviewed below: text surfaces, purple fills, and the phase marks. */
const REVIEWED_BACKGROUNDS = new Set([
  'scan-canvas',
  'scan-well',
  'scan-raised',
  'scan-badge',
  'scan-needs-chip',
  'control-hover',
  'scan-accent',
  'scan-accent-hover',
  'scan-line',
  'scan-ok',
  'scan-building',
]);

describe('text on the pages reads at 4.5:1', () => {
  it('every text colour is one of the reviewed text tokens, and white-on-purple only on a purple fill', () => {
    const colours = decls.filter(([, prop]) => prop === 'color');
    expect(colours.length).toBeGreaterThan(20);
    for (const [selector, , value] of colours) {
      // a changed line's tokens INHERIT the line's diff colour (paired below), and only there
      if (value === 'inherit') {
        expect(selector, 'inherit only on a changed line').toMatch(/data-kind='(add|del)'/);
        continue;
      }
      const token = tokenOf(value);
      expect(['scan-text', 'scan-muted', 'scan-heading', 'on-accent', 'scan-hunk'], `${selector} color ${value}`).toContain(token);
      if (token === 'on-accent') expect(selector, 'on-accent only on the primary fill').toMatch(/primary/);
    }
  });
  it('text, muted and heading on every page surface', () => {
    for (const s of PAGE_SURFACES)
      for (const t of ['scan-text', 'scan-muted', 'scan-heading'])
        expect(ratio(t, s), `${t} on ${s}`).toBeGreaterThanOrEqual(AA);
  });
  it('text on the selected fills: the quoted ask and harness tag, the limit banner, and a hovered button', () => {
    expect(ratio('scan-text', 'scan-badge')).toBeGreaterThanOrEqual(AA);
    expect(ratio('scan-text', 'scan-needs-chip')).toBeGreaterThanOrEqual(AA);
    expect(ratio('scan-text', 'control-hover')).toBeGreaterThanOrEqual(AA);
  });
  it('a changed line, every token in its diff colour on its own fill (the colour a changed comment now inherits)', () => {
    expect(ratio('scan-diff-add', 'scan-diff-add-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('scan-diff-del', 'scan-diff-del-bg')).toBeGreaterThanOrEqual(AA);
  });
  it('the hunk header on the raised Old/New row', () => {
    expect(ratio('scan-hunk', 'scan-raised')).toBeGreaterThanOrEqual(AA);
  });
  it('white on the purple fill and its hover (the only way purple carries text)', () => {
    expect(ratio('on-accent', 'scan-accent')).toBeGreaterThanOrEqual(AA);
    expect(ratio('on-accent', 'scan-accent-hover')).toBeGreaterThanOrEqual(AA);
  });
  it('every background the stylesheet uses is a reviewed surface (a new one must be paired above first)', () => {
    for (const [selector, prop, value] of decls.filter(([, p]) => p === 'background')) {
      if (value === 'transparent') continue;
      expect(REVIEWED_BACKGROUNDS, `${selector} ${prop}: ${value}`).toContain(tokenOf(value));
    }
  });
  it('a muted chip keeps its 4.5:1 on hover: the hover surface is the raised one, not the hover tone', () => {
    expect(rule('.di-chip:hover:not(:disabled)')).toContain('background: var(--di-scan-raised)');
    expect(ratio('scan-muted', 'scan-raised')).toBeGreaterThanOrEqual(AA);
  });
});

describe('purple is a fill only (Home tokens, D-2)', () => {
  it('the accent appears only as a background, never as text, an edge, a ring or a mark', () => {
    const accent = decls.filter(([, , value]) => /--di-(scan-)?accent(-hover)?\)/.test(value));
    expect(accent.length).toBeGreaterThan(0);
    for (const [selector, prop] of accent) expect(prop, selector).toBe('background');
  });
});

describe('edges, rings and state marks are visible (3:1)', () => {
  it('every bordered control takes the 3:1 control edge', () => {
    for (const selector of ['.di-chip', '.di-page-button', '.di-page-back', '.di-page-input', '.di-new-ask'])
      expect(rule(selector), selector).toContain('border: 1px solid var(--di-scan-edge)');
    for (const s of PAGE_SURFACES) expect(ratio('scan-edge', s), `edge on ${s}`).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the current sibling is marked with the state underline, not purple', () => {
    expect(rule(".di-scan-sibling[aria-current='page']")).toContain(
      'border-bottom-color: var(--di-scan-underline)',
    );
    expect(ratio('scan-underline', 'scan-canvas')).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('focus is never removed here: the pages keep the panel focus ring', () => {
    expect(raw).not.toMatch(/outline:\s*(none|0)\b/);
    expect(raw).not.toMatch(/:focus\s*\{/);
    for (const s of PAGE_SURFACES) expect(ratio('focus', s), `focus on ${s}`).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('no colour is written as a literal: every colour is a shared token', () => {
    expect(raw).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(/);
    expect(resolved).toBeTruthy();
  });
});
