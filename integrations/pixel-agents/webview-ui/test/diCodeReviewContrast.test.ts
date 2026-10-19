/**
 * Item 3 (original 20): "strong contrast" as arithmetic. Every colour of the review modal is a
 * SOLID token on `.di-rv` in `diCodeReview.css`; this test parses that very file and checks every
 * text-on-background pairing the component can produce against WCAG 2.1: 4.5:1 for text (AA), and
 * 3:1 for the focus ring and the borders that mark a boundary. Change a token and this checks it.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const css = fs.readFileSync(path.join(root, 'src/components/diCodeReview.css'), 'utf-8');

/** The `--rv-*` tokens of the first `.di-rv { ... }` rule, solid `#rrggbb` only. */
function tokens(): Record<string, string> {
  const rule = /\n\.di-rv \{([\s\S]*?)\n\}/.exec(css);
  if (!rule) throw new Error('the .di-rv rule was not found');
  const found: Record<string, string> = {};
  for (const m of rule[1].matchAll(/--rv-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) found[m[1]] = m[2];
  return found;
}
const T = tokens();

const AA = 4.5;
const BOUNDARY = 3;

function ratio(foreground: string, background: string): number {
  const fg = T[foreground];
  const bg = T[background];
  if (fg === undefined || bg === undefined) throw new Error(`no token ${foreground} / ${background}`);
  return contrastRatio(fg, bg);
}

describe('the stylesheet’s colour tokens are solid and complete', () => {
  it('every token the component relies on is defined as #rrggbb', () => {
    for (const name of [
      'bg', 'surface', 'surface-2', 'border', 'border-soft', 'text', 'muted', 'link', 'focus',
      'add-bg', 'add-gutter', 'add-mark', 'del-bg', 'del-gutter', 'del-mark', 'ctx-bg',
      'ctx-gutter', 'hunk-bg', 'hunk-text', 'empty-bg', 'st-added', 'st-removed', 'st-changed',
      'st-renamed', 'st-new', 'st-conflict', 'info-bg', 'warn-bg', 'block-bg', 'ok-bg',
      'tok-keyword', 'tok-string', 'tok-comment', 'tok-number', 'tok-type', 'tok-function',
      'tok-punct', 'tok-property', 'tok-tag', 'tok-attr', 'ctl-bg', 'ctl-text',
      'scroll-thumb', 'scroll-track',
    ]) {
      expect(T[name], name).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});

describe('text is readable on every surface it appears on (WCAG AA, 4.5:1)', () => {
  const surfaces = ['bg', 'surface', 'surface-2', 'ctx-bg', 'add-bg', 'del-bg', 'hunk-bg', 'info-bg', 'warn-bg', 'block-bg', 'ok-bg'];
  it('primary text on every surface', () => {
    for (const surface of surfaces) expect(ratio('text', surface), `text on ${surface}`).toBeGreaterThanOrEqual(AA);
  });
  it('muted text on the surfaces it is used on', () => {
    for (const surface of ['bg', 'surface', 'surface-2', 'ctx-bg', 'ctx-gutter', 'info-bg']) {
      expect(ratio('muted', surface), `muted on ${surface}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('links and the active toggle', () => {
    for (const surface of ['bg', 'surface', 'surface-2']) {
      expect(ratio('link', surface), `link on ${surface}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('line numbers on their gutters', () => {
    expect(ratio('muted', 'ctx-gutter')).toBeGreaterThanOrEqual(AA);
    expect(ratio('text', 'add-gutter')).toBeGreaterThanOrEqual(AA);
    expect(ratio('text', 'del-gutter')).toBeGreaterThanOrEqual(AA);
    expect(ratio('muted', 'empty-bg')).toBeGreaterThanOrEqual(AA);
  });
  it('the +/- marks and counts on added, removed and plain lines', () => {
    expect(ratio('add-mark', 'add-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('del-mark', 'del-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('add-mark', 'surface')).toBeGreaterThanOrEqual(AA);
    expect(ratio('del-mark', 'surface')).toBeGreaterThanOrEqual(AA);
    expect(ratio('add-mark', 'hunk-bg')).toBeGreaterThanOrEqual(AA);
    expect(ratio('del-mark', 'hunk-bg')).toBeGreaterThanOrEqual(AA);
  });
  it('the section header', () => {
    expect(ratio('hunk-text', 'hunk-bg')).toBeGreaterThanOrEqual(AA);
  });
  it('the headings of the two sides and the hint to scroll a long line sideways', () => {
    expect(ratio('text', 'surface')).toBeGreaterThanOrEqual(AA);
    expect(ratio('muted', 'surface')).toBeGreaterThanOrEqual(AA);
    expect(ratio('st-changed', 'surface')).toBeGreaterThanOrEqual(AA);
  });
  it('the status letters on their glyph backgrounds', () => {
    for (const status of ['st-added', 'st-removed', 'st-changed', 'st-renamed', 'st-new', 'st-conflict']) {
      expect(ratio('bg', status), `status ${status}`).toBeGreaterThanOrEqual(AA);
    }
  });
  it('the placeholder for a hidden character', () => {
    expect(ratio('ctl-text', 'ctl-bg')).toBeGreaterThanOrEqual(AA);
    // and it stands apart from every line background it can sit on
    for (const line of ['ctx-bg', 'add-bg', 'del-bg']) {
      expect(ratio('ctl-bg', line), `placeholder on ${line}`).toBeGreaterThanOrEqual(BOUNDARY);
    }
  });
});

describe('syntax colours are readable on plain, added and removed lines', () => {
  const kinds = ['keyword', 'string', 'comment', 'number', 'type', 'function', 'punct', 'property', 'tag', 'attr'];
  for (const line of ['ctx-bg', 'add-bg', 'del-bg']) {
    it(`every token class on ${line}`, () => {
      for (const kind of kinds) {
        expect(ratio(`tok-${kind}`, line), `${kind} on ${line}`).toBeGreaterThanOrEqual(AA);
      }
    });
  }
  it('the token classes are distinguishable from the plain text colour and from one another', () => {
    const colours = new Set(kinds.map((k) => T[`tok-${k}`].toLowerCase()));
    expect(colours.size).toBeGreaterThanOrEqual(8);
    expect(colours.has(T['text'].toLowerCase())).toBe(false);
  });
});

describe('the focus ring and the boundaries are visible (3:1)', () => {
  it('the focus ring against every surface a focused control sits on', () => {
    for (const surface of ['bg', 'surface', 'surface-2', 'ctx-bg', 'add-bg', 'del-bg', 'hunk-bg', 'info-bg', 'warn-bg', 'block-bg']) {
      expect(ratio('focus', surface), `focus ring on ${surface}`).toBeGreaterThanOrEqual(BOUNDARY);
    }
  });
  it('the borders that mark a control or a region against what is behind them', () => {
    expect(ratio('border', 'bg')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('border', 'surface')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('border', 'surface-2')).toBeGreaterThanOrEqual(BOUNDARY - 0.5);
  });
});

describe('the scrollbars of the reading panes are visible controls (3:1)', () => {
  it('the thumb against its track, and the track against the pane beside it', () => {
    expect(ratio('scroll-thumb', 'scroll-track')).toBeGreaterThanOrEqual(BOUNDARY);
    expect(ratio('scroll-thumb', 'ctx-bg')).toBeGreaterThanOrEqual(BOUNDARY);
  });
  it('the panes draw them with the pseudo-elements (the standard properties would switch Chromium to overlay bars)', () => {
    expect(css).toMatch(/\.di-rv-code-scroll::-webkit-scrollbar-thumb[\s\S]*?background: var\(--rv-scroll-thumb\);/);
    expect(css).toMatch(/\.di-rv-code-scroll::-webkit-scrollbar,[\s\S]*?background: var\(--rv-scroll-track\);/);
    // scrollbar-color only as the fallback for an engine without the pseudo-elements
    expect(css).toMatch(/@supports not selector\(::-webkit-scrollbar\) \{[\s\S]*?scrollbar-color: var\(--rv-scroll-thumb\) var\(--rv-scroll-track\);/);
    const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
    const outside = code.replace(/@supports not selector\(::-webkit-scrollbar\) \{[\s\S]*?\n\}/, '');
    expect(outside).not.toContain('scrollbar-color');
    expect(outside).not.toContain('scrollbar-width');
  });
});

describe('the explanation of a one-column window is ordinary, readable text', () => {
  it('is drawn like the rest of the toolbar text: muted on the surface at 4.5:1, never hidden or restyled to nothing', () => {
    expect(ratio('muted', 'surface')).toBeGreaterThanOrEqual(AA);
    const rule = /\.di-rv-layout-note \{([^}]*)\}/.exec(css);
    expect(rule, 'the note has a rule').not.toBeNull();
    const body = rule![1];
    expect(body).toContain('color: var(--rv-muted);');
    expect(body).not.toMatch(/display:\s*none|visibility:\s*hidden|opacity:|clip|position:\s*absolute|font-size:\s*(?:[0-9]|1[01])px/);
  });
});

describe('the side-by-side layout cannot be wider than its dialog', () => {
  it('two equal tracks that may shrink below their content (a plain 1fr cannot)', () => {
    expect(css).toMatch(/\.di-rv-panes \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);/s);
    expect(css).toMatch(/\.di-rv-pane-heads,\s*\.di-rv-panes \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);/);
    expect(css).not.toMatch(/grid-template-columns:\s*1fr 1fr/);
  });
  it('no rule derives a row\u2019s minimum width from the longest line (the mechanism that overflowed)', () => {
    expect(css).not.toContain('--rv-longest');
    expect(css).not.toMatch(/\.di-rv-half \{[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.di-rv-panes > \.di-rv-code-scroll \{[^}]*min-width: 0;/);
  });
});

describe('the stylesheet keeps its promises', () => {
  it('draws a focus ring on every focusable control, and removes the browser\u2019s only to replace it', () => {
    expect(css).toMatch(/\.di-rv :focus-visible \{[^}]*outline: 3px solid var\(--rv-focus\)/);
    // the ONE rule that drops the default outline is `.di-rv :focus`, immediately followed by its
    // replacement; nothing else in the stylesheet removes an outline
    const removals = css.match(/outline:\s*(none|0)\s*;/g) ?? [];
    expect(removals).toHaveLength(1);
    expect(css).toMatch(/\.di-rv :focus \{\s*outline: none;\s*\}\s*\.di-rv :focus-visible \{/);
  });
  it('honours reduced motion', () => {
    expect(css).toContain('@media (prefers-reduced-motion: reduce)');
  });
  it('uses solid tokens for text and diff backgrounds (no alpha that a test could not check)', () => {
    const rule = /\n\.di-rv \{([\s\S]*?)\n\}/.exec(css)![1];
    expect(rule).not.toMatch(/--rv-[a-z0-9-]+:\s*(rgba?|hsla?)\(/);
    expect(rule).not.toMatch(/--rv-[a-z0-9-]+:\s*#[0-9a-fA-F]{8}\b/);
  });
  it('keeps every scroll inside the dialog: the dialog itself clips, the page never scrolls', () => {
    expect(css).toMatch(/\.di-rv \{[\s\S]*?overflow: hidden;/);
    expect(css).toMatch(/\.di-rv-code-scroll \{[\s\S]*?overflow: auto;/);
    expect(css).toMatch(/\.di-rv-tree \{[\s\S]*?overflow: auto;/);
    expect(css).toContain('overscroll-behavior: contain');
  });
  it('lays the reading area out by grid/flex with min-width: 0 so long lines scroll inside it', () => {
    expect(css).toMatch(/\.di-rv-diff-pane \{[\s\S]*?min-width: 0;/);
    expect(css).toMatch(/\.di-rv-body \{[\s\S]*?min-height: 0;/);
  });
  it('uses the reading faces: a system sans for text and a monospace for code, never the pixel font', () => {
    expect(css).toContain('--rv-font-ui: system-ui');
    expect(css).toContain('--rv-font-code: ui-monospace');
    expect(css).not.toMatch(/FS Pixel Sans/);
    expect(css).toMatch(/\.di-rv \*,[\s\S]*?font-family: inherit;/);
  });
});
