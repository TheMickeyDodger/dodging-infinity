/**
 * A1: the DI chrome is re-pointed at the HOME token set, the visual source of truth
 * (`reconciled-free-ui-v3/01-home.jpg`, CONTROL-MAP "Locked rules", RECONCILE.md): background #0d0f1b,
 * purple #552df0, warm text #e6e2d6, muted #8b919e, Inter with the system sans as its fallback.
 *
 * The tokens are defined ONCE in index.css :root and read through the ONE shared reader (diChromeTokens.ts),
 * which fails loudly on a missing, duplicated, looping or fallback-shadowed token. The expected values below are
 * the design's literals, written by hand; nothing here derives them from the stylesheet under test.
 *
 * Purple is a FILL token only: about 2.75:1 on the Home background, it clears neither the 4.5:1 text bar nor the
 * 3:1 bar for a control edge or a focus ring (AC-UX-09). It may fill a surface behind near-white text, and it may
 * draw the wordmark. The guard below reads every chrome stylesheet and fails on any other use.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import { sharedTokens, sharedValue } from './diChromeTokens.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file: string) => fs.readFileSync(path.join(root, 'src/components', file), 'utf-8');

/** The Home design's literals (01-home.jpg, CONTROL-MAP locked rules), kept in a fixture (see its note). */
const HOME = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/home-tokens.FIXTURE.json'), 'utf-8'),
) as { bg: string; purple: string; warm: string; muted: string; white: string };
const WHITE = HOME.white;

const CHROME_STYLESHEETS = [
  'diOperationalPanel.css',
  'diMissionRequest.css',
  'diCodeReview.css',
  'diScanPanel.css',
] as const;

/**
 * The only selectors allowed to draw IN purple: the DI wordmark (a logo, outside the contrast rule, and named
 * by the brief as purple's second use).
 */
const WORDMARK_SELECTORS = ['.di-scan-mark', '.di-rail-wordmark-di'];

/** Properties that draw text, an edge or a ring in a colour (as opposed to filling a surface). */
const DRAWN_IN_COLOUR =
  /^(?:color|border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?(?:-color)?|outline(?:-color)?|text-decoration(?:-color)?|caret-color|column-rule(?:-color)?|stroke|text-emphasis-color|-webkit-text-fill-color)$/;

interface Declaration {
  selector: string;
  property: string;
  value: string;
}

/** Every declaration of a stylesheet with its rule's selector (comments removed; nesting inside at-rules followed). */
function declarations(css: string): Declaration[] {
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out: Declaration[] = [];
  for (const rule of code.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = rule[1].replace(/\s+/g, ' ').trim();
    for (const decl of rule[2].split(';')) {
      const colon = decl.indexOf(':');
      if (colon < 0) continue;
      out.push({
        selector,
        property: decl.slice(0, colon).trim(),
        value: decl
          .slice(colon + 1)
          .replace(/\s+/g, ' ')
          .trim(),
      });
    }
  }
  return out;
}

/**
 * A value with every `var(--x)` replaced: `--di-*` through the shared reader, a stylesheet's own palette names
 * through its own definitions. Returns every possible result (a local name defined in several rules has several).
 */
function resolvedValues(
  value: string,
  local: Map<string, string[]>,
  seen: string[] = [],
): string[] {
  const m = /var\(\s*--([a-z0-9-]+)\s*\)/.exec(value);
  if (!m) return [value];
  const name = m[1];
  if (seen.includes(name)) throw new Error(`local chain loops: ${[...seen, name].join(' -> ')}`);
  const options = name.startsWith('di-')
    ? [sharedValue(name.slice(3))]
    : (local.get(name) ?? []).flatMap((v) => resolvedValues(v, local, [...seen, name]));
  if (options.length === 0) throw new Error(`--${name} is not defined in this stylesheet`);
  return options.flatMap((option) => resolvedValues(value.replace(m[0], option), local, seen));
}

/** Where a stylesheet draws text, an edge or a ring in purple, outside the wordmark. */
function purpleDrawn(css: string, label: string): string[] {
  const decls = declarations(css);
  const local = new Map<string, string[]>();
  for (const d of decls)
    if (d.property.startsWith('--') && !d.property.startsWith('--di-'))
      local.set(d.property.slice(2), [...(local.get(d.property.slice(2)) ?? []), d.value]);
  const found: string[] = [];
  for (const d of decls) {
    if (d.property.startsWith('--')) continue;
    const wordmark = d.selector
      .split(',')
      .every((part) => WORDMARK_SELECTORS.some((s) => part.trim().startsWith(s)));
    if (DRAWN_IN_COLOUR.test(d.property)) {
      if (wordmark && d.property === 'color') continue;
      for (const v of resolvedValues(d.value, local))
        if (v.toLowerCase().includes(HOME.purple))
          found.push(`${label} ${d.selector} { ${d.property}: ${d.value} }`);
    }
    if (d.property === 'box-shadow' || d.property === 'outline') {
      // a ring: `0 0 0 <n>px <colour>` (a spread with no offset and no blur), per comma-separated layer
      for (const v of resolvedValues(d.value, local))
        for (const layer of v.split(/,(?![^(]*\))/)) {
          const ring = /^\s*0 0 0 \d+(?:\.\d+)?px (.+?)\s*$/.exec(layer);
          if (ring && ring[1].toLowerCase() === HOME.purple)
            found.push(`${label} ${d.selector} { ${d.property}: ${d.value} } (a ring)`);
        }
    }
  }
  return found;
}

describe('A1: the shared chrome tokens carry the Home values (CONTROL-MAP locked rules)', () => {
  const tokens = sharedTokens();
  it('the background, purple, warm text and muted text are the Home literals', () => {
    expect(tokens.bg).toBe(HOME.bg);
    expect(tokens.accent).toBe(HOME.purple);
    expect(tokens.text).toBe(HOME.warm);
    expect(tokens.muted).toBe(HOME.muted);
  });
  it('the reading face is Inter, then the system sans (never the pixel font)', () => {
    const face = sharedValue('font-ui');
    expect(face.startsWith('Inter, system-ui,')).toBe(true);
    expect(face.endsWith('sans-serif')).toBe(true);
    expect(face).not.toMatch(/FS Pixel Sans/);
  });
  it('every chrome palette (operational panel, request workspace, code review, scan panel) resolves to the Home background and text', () => {
    const pairs: Array<[string, string, string]> = [
      ['diOperationalPanel.css', '--op-bg', HOME.bg],
      ['diOperationalPanel.css', '--op-text', HOME.warm],
      ['diOperationalPanel.css', '--op-muted', HOME.muted],
      ['diMissionRequest.css', '--mr-bg', HOME.bg],
      ['diMissionRequest.css', '--mr-text', HOME.warm],
      ['diMissionRequest.css', '--mr-primary-bg', HOME.purple],
      ['diCodeReview.css', '--rv-bg', HOME.bg],
      ['diCodeReview.css', '--rv-text', HOME.warm],
    ];
    for (const [file, name, want] of pairs) {
      const decl = declarations(read(file)).find((d) => d.property === name);
      expect(decl, `${file} defines ${name}`).toBeDefined();
      expect(resolvedValues(decl!.value, new Map()), `${file} ${name}`).toEqual([want]);
    }
    // the scan panel (Home missions panel, left rail, Mission details) reads the shared scan tokens directly
    expect(sharedValue('scan-bg')).toBe(HOME.bg);
    expect(sharedValue('scan-canvas')).toBe(HOME.bg);
    expect(sharedValue('scan-text')).toBe(HOME.warm);
    expect(sharedValue('scan-muted')).toBe(HOME.muted);
    expect(sharedValue('scan-accent')).toBe(HOME.purple);
  });
  it('the reading face comes from --di-font-ui only: no chrome stylesheet re-declares Inter or a sans face of its own', () => {
    const ownSans = /\bInter\b|system-ui|-apple-system|sans-serif|Segoe UI|Helvetica|Arial|Roboto/;
    const found: string[] = [];
    for (const file of CHROME_STYLESHEETS)
      for (const d of declarations(read(file)))
        if ((d.property === 'font-family' || d.property === 'font') && ownSans.test(d.value))
          found.push(`${file} ${d.selector} { ${d.property}: ${d.value} }`);
    expect(found).toEqual([]);
  });
});

describe('A1: the Home purple is a fill token only (AC-UX-09)', () => {
  it('why: on the Home background it fails the text bar and the edge bar, and behind white text it passes', () => {
    // hand-checked: purple is 2.74:1 on the Home background; white on purple is 6.95:1
    expect(contrastRatio(HOME.purple, HOME.bg)).toBeLessThan(3);
    expect(contrastRatio(WHITE, HOME.purple)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(sharedValue('on-accent'), sharedValue('accent'))).toBeGreaterThanOrEqual(
      4.5,
    );
  });
  it('the guard has teeth: purple text, a purple edge and a purple ring are each found; a purple fill and the wordmark are not', () => {
    // the sample spells purple itself, so the guard is proven independently of the token values under test
    const sample = [
      `.e { --x-accent: ${HOME.purple}; --x-on: ${WHITE}; }`,
      '.a { color: var(--x-accent); }',
      '.b { border: 1px solid var(--x-accent); }',
      '.c { box-shadow: 0 0 0 2px var(--x-accent); }',
      '.d { background: var(--x-accent); color: var(--x-on); }',
      '.di-scan-mark { color: var(--x-accent); }',
    ].join('\n');
    expect(purpleDrawn(sample, 'sample').map((line) => line.split(' ')[1])).toEqual([
      '.a',
      '.b',
      '.c',
    ]);
  });
  it('no chrome stylesheet draws text, a control edge or a focus ring in purple (the wordmark aside)', () => {
    const found = CHROME_STYLESHEETS.flatMap((file) => purpleDrawn(read(file), file));
    expect(found).toEqual([]);
  });
});
