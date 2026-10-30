/**
 * AC-UX-09 for the scan face (the Home missions panel, the left rail and Mission details), as arithmetic.
 * WCAG 2.2 AA: 4.5:1 for text; 3:1 for a focus ring, a control's edge and a mark that carries state.
 *
 * Every colour the scan face uses is a shared `--di-scan-*` (or `--di-*`) token in index.css :root, read through the
 * ONE shared reader (diChromeTokens.ts), so these pairs are the values the browser paints. The thresholds are WCAG's;
 * none is lowered to fit a token. Status is never colour alone (AC-UX-10): every dot sits beside its word, so the
 * dots themselves are not asserted as text.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import { resolveShared, sharedValue, SOLID } from './diChromeTokens.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const written = fs.readFileSync(path.join(root, 'src/components/diScanPanel.css'), 'utf-8');
/** The stylesheet with every shared token resolved (throws on any it cannot resolve). */
const code = resolveShared(written).replace(/\/\*[\s\S]*?\*\//g, '');
/** diScanPages.css (the view-scoped Back, header and page rules), resolved the same way. */
const pages = resolveShared(
  fs.readFileSync(path.join(root, 'src/components/diScanPages.css'), 'utf-8'),
).replace(/\/\*[\s\S]*?\*\//g, '');

const AA = 4.5;
const BOUNDARY = 3;

/** A token's final colour (`scan-bg` reads `--di-scan-bg`). */
const c = (name: string): string => {
  const value = sharedValue(name);
  expect(value, `--di-${name} is a solid colour`).toMatch(SOLID);
  return value;
};
const ratio = (fg: string, bg: string) => contrastRatio(c(fg), c(bg));

/** Every surface scan text is drawn on: the panel, the Mission stage, a card or well, a control, a selected row. */
const SURFACES = ['scan-bg', 'scan-canvas', 'scan-well', 'scan-raised', 'scan-selected'];

/** A rule's declarations as written (resolved), by exact selector. */
function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`(?:^|\\n|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(code);
  if (!m) throw new Error(`no rule ${selector} in diScanPanel.css`);
  return m[1];
}

/** Every rule of a resolved stylesheet whose selector list names the Back control, with any state, child or view scope. */
function backRules(css: string): { selector: string; body: string }[] {
  const out: { selector: string; body: string }[] = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].replace(/\s+/g, ' ').trim();
    if (/\.di-scan-back(?![\w-])/.test(selector)) out.push({ selector, body: m[2] });
  }
  return out;
}
const STYLESHEETS = [
  ['diScanPanel.css', code],
  ['diScanPages.css', pages],
] as const;

describe('scan text is readable on every surface it is drawn on (4.5:1)', () => {
  it('warm text and muted text on every scan surface', () => {
    for (const s of SURFACES) {
      expect(ratio('scan-text', s), `text on ${s}`).toBeGreaterThanOrEqual(AA);
      expect(ratio('scan-muted', s), `muted on ${s}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('near-white text on the purple fill and its hover (the only way purple carries text)', () => {
    expect(ratio('on-accent', 'scan-accent')).toBeGreaterThanOrEqual(AA);
    expect(ratio('on-accent', 'scan-accent-hover')).toBeGreaterThanOrEqual(AA);
  });
  it('every status pill word on its chip, and on its solid fill', () => {
    for (const chip of [
      'scan-chip',
      'scan-needs-chip',
      'scan-building-chip',
      'scan-ok-chip',
      'scan-bad-chip',
    ])
      expect(ratio('scan-text', chip), `pill word on ${chip}`).toBeGreaterThanOrEqual(AA);
    // scan-chip is also the solid fill of a neutral (idle) status pill
    for (const fill of [
      'scan-needs-fill',
      'scan-building-fill',
      'scan-ok-fill',
      'scan-bad-fill',
      'scan-chip',
    ])
      expect(ratio('on-accent', fill), `solid pill word on ${fill}`).toBeGreaterThanOrEqual(AA);
  });
  it('the count badges: the Open tab count, and the Reception count on the rail', () => {
    expect(ratio('scan-text', 'scan-badge')).toBeGreaterThanOrEqual(AA);
    expect(ratio('scan-ink', 'scan-attn')).toBeGreaterThanOrEqual(AA);
  });
  it('status words drawn in their own colour (the diff counts) on the stage', () => {
    expect(ratio('scan-ok', 'scan-canvas')).toBeGreaterThanOrEqual(AA);
    expect(ratio('scan-bad', 'scan-canvas')).toBeGreaterThanOrEqual(AA);
  });
});

describe('edges, rings and state marks are visible (3:1)', () => {
  it('the focus ring on every scan surface', () => {
    for (const s of SURFACES)
      expect(ratio('focus', s), `focus on ${s}`).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('a control edge on every scan surface', () => {
    for (const s of SURFACES)
      expect(ratio('scan-edge', s), `edge on ${s}`).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the selected tab underline and the selected row border, which mark state', () => {
    expect(ratio('scan-underline', 'scan-bg')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('scan-yellow', 'scan-bg')).toBeGreaterThanOrEqual(BOUNDARY);
  });
});

describe('the rules paint with those tokens', () => {
  it('focus is drawn with the focus token, never removed without a replacement', () => {
    expect(rule('.di-scan :focus-visible')).toContain(`outline: 2px solid ${c('focus')}`);
  });
  it('the BASE rule of every bordered control the scan face draws takes the 3:1 control-edge token (diScanPanel.css only)', () => {
    // rule() reads ONE exact base selector in diScanPanel.css and does not apply the cascade, so a view-scoped override
    // (diScanPages.css: the Back control on Mission details, Code Diff and Comments) can change the rendered border
    // while this stays green. It says each base rule uses the token whose 3:1 ratio is asserted above; it says
    // nothing about what any view renders.
    for (const selector of [
      '.di-scan-back',
      '.di-scan-search input',
      '.di-scan-trash-boxed',
      '.di-scan-action-secondary',
      '.di-scan-step-action',
      '.di-scan-state-action',
    ])
      expect(rule(selector), selector).toMatch(new RegExp(`border: 1px solid ${c('scan-edge')}`));
  });
  it('the Back control paints its identifying content with the shared text token, and no other rule for it recolours it (the Comments text link aside)', () => {
    // its chevron is drawn in currentColor, so the one `color` on the control is what identifies it
    expect(rule('.di-scan-back')).toContain(`color: ${c('scan-text')}`);
    expect(backRules(code)[0].body).toBe(rule('.di-scan-back'));
    for (const [file, css] of STYLESHEETS) {
      const rules = backRules(css);
      rules.forEach(({ selector, body }, i) => {
        if (file === 'diScanPanel.css' && i === 0) return; // the base rule, asserted above
        if (selector.includes("[data-view='comments']")) return; // a muted text link by design
        expect(body, `${file}: ${selector}`).not.toMatch(/(?:^|[;\s])(?:color|fill|stroke)\s*:/);
      });
    }
  });
  it('the Back control never removes its focus outline: no rule for it sets the outline to none, hidden, zero or transparent', () => {
    for (const [file, css] of STYLESHEETS)
      for (const { selector, body } of backRules(css))
        expect(body, `${file}: ${selector}`).not.toMatch(
          /(?:^|[;\s])outline(?:-style|-width|-color)?\s*:\s*(?:none|hidden|transparent|0(?:px)?)(?![\w.])/,
        );
  });
  it('the tab underline and the rail current-place mark use the state tokens, not purple', () => {
    expect(rule(".di-scan-tab[aria-selected='true']")).toContain(
      `border-bottom-color: ${c('scan-underline')}`,
    );
    expect(rule(".di-rail-item[aria-current='page']")).toContain(
      `box-shadow: inset 3px 0 0 ${c('scan-underline')}`,
    );
  });
});
