/**
 * The shared DI chrome tokens (index.css :root) and the ONE reader the chrome contrast tests resolve them through
 * (diChromeTokens.ts). The tokens are defined once; every colour is solid; nothing carries an alpha literal a test
 * could not check; and the reader fails loudly rather than degrade.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import { ALPHA, INDEX_CSS, resolveShared, sharedTokens, sharedValue,SOLID } from './diChromeTokens.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const tokens = sharedTokens();
const names = Object.keys(tokens);
/** Tokens whose value is a colour (a hex literal). */
const colours = names.filter((n) => tokens[n].startsWith('#'));
/** Tokens composed of other tokens (shadows, the glow). */
const composites = names.filter((n) => /var\(--di-/.test(tokens[n]));

describe('the shared chrome tokens are defined once and are checkable', () => {
  it('defines the colour, type, spacing, radius, elevation and attention families', () => {
    for (const family of ['bg', 'surface', 'control', 'edge', 'text', 'muted', 'link', 'focus', 'accent', 'ok', 'run', 'perm', 'warn', 'bad'])
      expect(tokens[family], family).toMatch(SOLID);
    for (const n of ['font-ui', 'font-code', 'text-label', 'text-base', 'space-3', 'radius', 'radius-card', 'radius-pill', 'shadow-pop', 'glow'])
      expect(tokens[n], n).toBeDefined();
  });
  it('every colour token is a solid #rrggbb', () => {
    expect(colours.length).toBeGreaterThan(25);
    for (const n of colours) expect(tokens[n], `--di-${n}`).toMatch(SOLID);
  });
  it('no token carries an alpha colour (rgba, hsla, 8- or 4-digit hex), as written or resolved', () => {
    for (const n of names) {
      expect(tokens[n], `--di-${n} as written`).not.toMatch(ALPHA);
      expect(sharedValue(n), `--di-${n} resolved`).not.toMatch(ALPHA);
    }
  });
  it('a composite (shadow, glow) is built only from solid colour tokens', () => {
    expect(composites.length).toBeGreaterThanOrEqual(4);
    for (const n of composites)
      for (const m of tokens[n].matchAll(/var\(--di-([a-z0-9-]+)\)/g))
        expect(tokens[m[1]], `--di-${n} uses --di-${m[1]}`).toMatch(SOLID);
  });
  it('no chrome stylesheet defines a --di-* token of its own (one definition, in index.css)', () => {
    for (const file of [
      'diOperationalPanel.css',
      'diMissionRequest.css',
      'diCodeReview.css',
      'diScanPanel.css',
    ]) {
      const css = fs.readFileSync(path.join(root, 'src/components', file), 'utf-8');
      expect(css, file).not.toMatch(/--di-[a-z0-9-]+\s*:/);
    }
  });
  it('faint text and the control edge meet AA where the prototype did not (named corrections)', () => {
    // prototype faint #7d7d86 measured 4.02:1 on #1e1e2e; corrected
    expect(contrastRatio(tokens.faint, tokens.bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(tokens.faint, tokens.surface)).toBeGreaterThanOrEqual(4.5);
    // prototype control border: 12% white, no 3:1 boundary; corrected to an edge that marks a control
    for (const s of ['bg', 'surface', 'control', 'selected'])
      expect(contrastRatio(tokens.edge, tokens[s]), `edge on ${s}`).toBeGreaterThanOrEqual(3);
    // white on the primary action
    expect(contrastRatio(tokens['on-accent'], tokens.accent)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('the shared reader fails loudly, never degrades', () => {
  it('resolves a chain to its final value', () => {
    expect(resolveShared('a { color: var(--di-text); }')).toBe(`a { color: ${tokens.text}; }`);
    expect(sharedValue('glow')).not.toContain('var(');
  });
  it('throws on a token :root does not define', () => {
    expect(() => resolveShared('a { color: var(--di-no-such-token); }')).toThrow(/not defined/);
  });
  it('throws on a fallback, which could hide a missing token', () => {
    expect(() => resolveShared(`a { color: var(--di-text, ${tokens.bg}); }`)).toThrow(/fallback/);
  });
  it('throws on a token defined twice or outside :root, and on a chain that loops', () => {
    const twice = INDEX_CSS.replace(':root {', `:root {\n  --di-text: ${tokens.bg};`);
    expect(twice).not.toBe(INDEX_CSS);
    expect(() => sharedTokens(twice)).toThrow(/defined twice/);
    const outside = `${INDEX_CSS}\n.x {\n  --di-text: ${tokens.bg};\n}\n`;
    expect(() => sharedTokens(outside)).toThrow(/outside :root/);
    expect(() => sharedTokens('a { color: red; }')).toThrow(/no :root/);
    expect(() => sharedValue('glow', ['glow'])).toThrow(/loops/);
  });
});
