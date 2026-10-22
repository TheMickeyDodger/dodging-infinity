/**
 * The workspace's place in the window's layers, as RELATIONS read from the real sources (no browser).
 *
 * Beside the side panel the workspace covers no world overlay, so it keeps a low layer. Where it COVERS the window it must sit ABOVE every world overlay
 * (the ambient legend, the agents' labels, the world and city controls, the chips) so none of them letters over its text, and BELOW the greeter's intro
 * bubble and every modal and dialog so those still open over it. Every number here is read from where the layer is stated: the stylesheet, a Tailwind
 * class, an inline style or a named constant. A new overlay that states a layer above the beside workspace fails the sweep until someone decides which
 * side of the covering workspace it belongs on.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const read = (rel: string): string => fs.readFileSync(path.join(src, rel), 'utf-8');

function walk(dir: string, base = ''): string[] {
  const found: string[] = [];
  for (const entry of fs.readdirSync(path.join(dir, base), { withFileTypes: true })) {
    const rel = base === '' ? entry.name : `${base}/${entry.name}`;
    if (entry.isDirectory()) found.push(...walk(dir, rel));
    else if (/\.(tsx?|css)$/.test(entry.name)) found.push(rel);
  }
  return found.sort();
}

/** Code without its comments: a comment that MENTIONS a layer (as constants.ts does for the migration notice) is not a layer the source states. */
function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');
}

/** Every layer a source states as a number: a stylesheet z-index, a Tailwind z-NN class, an inline zIndex, a default parameter, a named *_Z_INDEX constant. */
function layersIn(rel: string): number[] {
  const text = withoutComments(read(rel));
  const found: number[] = [];
  if (rel.endsWith('.css')) {
    for (const m of text.matchAll(/z-index:\s*(\d+)\s*;/g)) found.push(Number(m[1]));
    return found;
  }
  for (const m of text.matchAll(/(?<![\w-])z-(\d+)(?![\w-])/g)) found.push(Number(m[1]));
  for (const m of text.matchAll(/\bzIndex(?:=\{|\s*[:=]\s*)(\d+)\b/g)) found.push(Number(m[1]));
  for (const m of text.matchAll(/\bzIndex:\s*\w+\s*\?\s*(\d+)\s*:\s*(\d+)/g))
    found.push(Number(m[1]), Number(m[2]));
  for (const m of text.matchAll(/\b[A-Z_]*Z_INDEX\s*=\s*(\d+)\b/g)) found.push(Number(m[1]));
  return found;
}

/** The z-index of one rule of the workspace's stylesheet. */
function ruleLayer(selector: string): number {
  const css = read('components/diMissionRequest.css');
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rule = new RegExp(`(?:^|\\n)${escaped} \\{([\\s\\S]*?)\\n\\}`).exec(css);
  if (!rule) throw new Error(`the rule ${selector} was not found`);
  const z = /\n\s*z-index:\s*(\d+)\s*;/.exec(`\n${rule[1]}`);
  if (!z) throw new Error(`the rule ${selector} states no z-index`);
  return Number(z[1]);
}

const constant = (name: string): number => {
  const m = new RegExp(`export const ${name} = (\\d+);`).exec(read('constants.ts'));
  if (!m) throw new Error(`no constant ${name}`);
  return Number(m[1]);
};

interface Layer {
  file: string;
  what: string;
  z: number[];
}

/** World overlays: they must sit BELOW the covering workspace. (Those at 21 or less sit below even the beside workspace, so only these need naming.) */
const WORLD_OVERLAYS: Layer[] = [
  { file: 'components/AmbientLegend.tsx', what: 'the ambient legend', z: [40] },
  { file: 'office/components/ToolOverlay.tsx', what: "the agents' labels", z: [41, 42] },
  { file: 'components/WorldViewControls.tsx', what: 'the world-view controls', z: [30] },
  { file: 'components/CityControls.tsx', what: 'the city controls', z: [30] },
];

/** The intro bubble, modals, notices and dialog backdrops: they must sit ABOVE the covering workspace. */
const ABOVE_THE_WORKSPACE: Layer[] = [
  { file: 'constants.ts', what: "the greeter's intro bubble (tour and consent)", z: [45] },
  { file: 'components/ui/Modal.tsx', what: 'the shared modal (Settings and others): backdrop, panel one above', z: [50] },
  { file: 'components/ChangelogModal.tsx', what: 'the changelog modal', z: [51] },
  { file: 'App.tsx', what: 'the fixture banner (hidden while covering) and the hooks modal', z: [50, 52] },
  { file: 'components/MigrationNotice.tsx', what: 'the migration notice', z: [100] },
  { file: 'components/diOperationalPanel.css', what: 'the inspector and the Reception inbox backdrops', z: [1000] },
  { file: 'components/diCodeReview.css', what: 'the code review backdrop', z: [1000] },
];

const BESIDE = ruleLayer('.di-mr');
const COVERING = ruleLayer(".di-mr[data-covering='true']");
const HIGHEST_OVERLAY = Math.max(...WORLD_OVERLAYS.flatMap((l) => l.z));
const LOWEST_ABOVE = Math.min(...ABOVE_THE_WORKSPACE.flatMap((l) => l.z));

describe('the workspace sits in the window’s layers: above the world overlays and below the dialogs, only where it covers', () => {
  it('beside the side panel it stays at a low layer (it covers none of the overlays there)', () => {
    expect(BESIDE).toBe(21);
    expect(BESIDE).toBeLessThan(COVERING);
  });

  it('covering the window, it is above EVERY world overlay (the legend, the agents’ labels, the controls)', () => {
    for (const layer of WORLD_OVERLAYS)
      for (const z of layer.z)
        expect(COVERING, `${layer.what} (${layer.file}, z ${z}) would letter over the covering workspace`).toBeGreaterThan(z);
    expect(COVERING).toBeGreaterThan(HIGHEST_OVERLAY);
    // the named ones, read straight from where they are stated
    expect(constant('INTRO_BUBBLE_Z_INDEX')).toBeGreaterThan(COVERING);
  });

  it('covering the window, it is below the intro bubble, every modal and every dialog backdrop', () => {
    for (const layer of ABOVE_THE_WORKSPACE)
      for (const z of layer.z)
        expect(COVERING, `${layer.what} (${layer.file}, z ${z}) must open over the covering workspace`).toBeLessThan(z);
    expect(COVERING).toBeLessThan(LOWEST_ABOVE);
  });

  it('the covering layer sits in the open gap between the two groups, so neither can reach it by a small change', () => {
    expect(HIGHEST_OVERLAY).toBeLessThan(COVERING);
    expect(COVERING).toBeLessThan(LOWEST_ABOVE);
    expect(LOWEST_ABOVE - HIGHEST_OVERLAY).toBeGreaterThanOrEqual(3);
  });

  it('the fixture banner (a layer ABOVE the covering workspace) is hidden while the workspace covers, and drawn again otherwise', () => {
    const css = read('components/diMissionRequest.css');
    expect(css).toMatch(
      /body:has\(\.di-mr\[data-covering='true'\]\) \.di-fixture-banner\s*\{[^}]*visibility:\s*hidden\s*;/,
    );
    expect(read('App.tsx')).toMatch(/di-fixture-banner[^"]*z-50/);
  });

  it('every layer ABOVE the beside workspace that the source states is classified: nothing new can slip between the groups unseen', () => {
    const classified = new Map<string, Set<number>>();
    for (const layer of [...WORLD_OVERLAYS, ...ABOVE_THE_WORKSPACE]) {
      const known = classified.get(layer.file) ?? new Set<number>();
      for (const z of layer.z) known.add(z);
      classified.set(layer.file, known);
    }
    const unclassified: string[] = [];
    for (const file of walk(src)) {
      if (file === 'components/diMissionRequest.css') continue; // the workspace's own two layers, read above
      for (const z of layersIn(file)) {
        if (z <= BESIDE) continue; // at or below the beside workspace: below it in both layouts
        if (!classified.get(file)?.has(z))
          unclassified.push(`${file}: z ${z}: decide whether it sits BELOW the covering workspace (add it to WORLD_OVERLAYS, at most ${COVERING - 1}) or ABOVE it (add it to ABOVE_THE_WORKSPACE, at least ${COVERING + 1})`);
      }
    }
    expect(unclassified).toEqual([]);
  });

  it('every classified layer is still stated where it was (the table is not stale)', () => {
    for (const layer of [...WORLD_OVERLAYS, ...ABOVE_THE_WORKSPACE]) {
      const stated = new Set(layersIn(layer.file));
      for (const z of layer.z)
        expect(stated.has(z), `${layer.what}: ${layer.file} no longer states z ${z}`).toBe(true);
    }
  });
});
