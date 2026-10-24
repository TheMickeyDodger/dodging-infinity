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

/**
 * The layers a piece of component source states through a Tailwind class: z-NN and the arbitrary-value form z-[NN], which is the SAME layer written
 * another way. Tolerant of syntax, strict about value: a z-[99] must be seen (an extractor that only knew z-NN would let it slip past the
 * completeness check unclassified), and the number read is the number the class sets.
 */
function classLayers(text: string): number[] {
  return [
    ...[...text.matchAll(/(?<![\w-])z-(\d+)(?![\w-])/g)].map((m) => Number(m[1])),
    ...[...text.matchAll(/(?<![\w-])z-\[(\d+)\](?![\w-])/g)].map((m) => Number(m[1])),
  ];
}

/** Every layer a source states as a number: a stylesheet z-index, a Tailwind z-NN or z-[NN] class, an inline zIndex, a default parameter, a named *_Z_INDEX constant. */
function layersIn(rel: string): number[] {
  const text = withoutComments(read(rel));
  const found: number[] = [];
  if (rel.endsWith('.css')) {
    for (const m of text.matchAll(/z-index:\s*(\d+)\s*;/g)) found.push(Number(m[1]));
    return found;
  }
  found.push(...classLayers(text));
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
  { file: 'office/components/ToolOverlay.tsx', what: "the agents' labels", z: [41, 42] },
  // the world's left strip: the camera-view controls, the bottom-left column (toolbar, fixture label, ambient legend) and the City popover in ONE grid. BEFORE, the controls and the
  // column each stated layer 30 (two entries: WorldViewControls.tsx and WorldStack.tsx) and were placed independently; AFTER, neither states a layer of its own and the strip states the
  // one layer (30). It is the same layer, below the covering workspace; the completeness checks below still catch any new layer in any of these files.
  {
    file: 'components/WorldLeftRail.tsx',
    what: "the world's left strip (camera-view controls, bottom-left column, City popover)",
    z: [30],
  },
  // AC3 (minimize and restore): the slim edge strip that holds the restore controls of minimized surfaces. DECIDED to sit BELOW the covering
  // workspace: it belongs to the side panel, so where the workspace covers the whole window it covers the strip together with the panel (the
  // controls are reachable again once the workspace is closed or minimized). Beside the panel the workspace is inset by the strip's width, so
  // they never overlap, and the strip (25) is above the beside layer (21) in any case, which keeps its restore controls reachable.
  { file: 'components/diOperationalPanel.css', what: 'the minimized-surface strip', z: [25] },
];

/** The intro bubble, modals, notices and dialog backdrops: they must sit ABOVE the covering workspace. */
const ABOVE_THE_WORKSPACE: Layer[] = [
  { file: 'constants.ts', what: "the greeter's intro bubble (tour and consent)", z: [45] },
  {
    file: 'components/ui/Modal.tsx',
    what: 'the shared modal (Settings and others): backdrop, panel one above',
    z: [50],
  },
  { file: 'components/ChangelogModal.tsx', what: 'the changelog modal', z: [51] },
  // BEFORE this also named the fixture banner (z 50, hidden while covering): the banner no longer has a layer of its own (it sits in WorldStack, layer 30 above, and
  // its structure and lifecycle are pinned below), so only the hooks modal's layer is stated in App.tsx. A layer re-added to the banner would be an UNCLASSIFIED
  // z in App.tsx and fail the completeness check, and fail the structural pin below.
  { file: 'App.tsx', what: 'the hooks modal', z: [52] },
  { file: 'components/MigrationNotice.tsx', what: 'the migration notice', z: [100] },
  {
    file: 'components/diOperationalPanel.css',
    what: 'the inspector and the Reception inbox backdrops',
    z: [1000],
  },
  { file: 'components/diCodeReview.css', what: 'the code review backdrop', z: [1000] },
];

const BESIDE = ruleLayer('.di-mr');
const COVERING = ruleLayer(".di-mr[data-covering='true']");
const HIGHEST_OVERLAY = Math.max(...WORLD_OVERLAYS.flatMap((l) => l.z));
const LOWEST_ABOVE = Math.min(...ABOVE_THE_WORKSPACE.flatMap((l) => l.z));

describe('the layer extractor itself', () => {
  it('reads a Tailwind layer in either syntax, and the number it reads is the number the class sets (syntax-tolerant, value-strict)', () => {
    expect(classLayers('className="absolute z-15 pixel-panel"')).toEqual([15]);
    expect(classLayers('className="absolute z-[15] pixel-panel"')).toEqual([15]);
    expect(
      classLayers('className="z-[99] top-8 z-40 -z-10 xz-5 z-[a] data-z-7"').sort((a, b) => a - b),
    ).toEqual([40, 99]);
    expect(classLayers('className="no layer here"')).toEqual([]);
  });
});

describe('the workspace sits in the window’s layers: above the world overlays and below the dialogs, only where it covers', () => {
  it('beside the side panel it stays at a low layer (it covers none of the overlays there)', () => {
    expect(BESIDE).toBe(21);
    expect(BESIDE).toBeLessThan(COVERING);
  });

  it('covering the window, it is above EVERY world overlay (the legend, the agents’ labels, the controls)', () => {
    for (const layer of WORLD_OVERLAYS)
      for (const z of layer.z)
        expect(
          COVERING,
          `${layer.what} (${layer.file}, z ${z}) would letter over the covering workspace`,
        ).toBeGreaterThan(z);
    expect(COVERING).toBeGreaterThan(HIGHEST_OVERLAY);
    // the named ones, read straight from where they are stated
    expect(constant('INTRO_BUBBLE_Z_INDEX')).toBeGreaterThan(COVERING);
  });

  it('covering the window, it is below the intro bubble, every modal and every dialog backdrop', () => {
    for (const layer of ABOVE_THE_WORKSPACE)
      for (const z of layer.z)
        expect(
          COVERING,
          `${layer.what} (${layer.file}, z ${z}) must open over the covering workspace`,
        ).toBeLessThan(z);
    expect(COVERING).toBeLessThan(LOWEST_ABOVE);
  });

  it('the covering layer sits in the open gap between the two groups, so neither can reach it by a small change', () => {
    expect(HIGHEST_OVERLAY).toBeLessThan(COVERING);
    expect(COVERING).toBeLessThan(LOWEST_ABOVE);
    expect(LOWEST_ABOVE - HIGHEST_OVERLAY).toBeGreaterThanOrEqual(3);
  });

  it("the fixture banner has NO layer of its own: it sits in the left strip's PINNED FOOTER (layer 30, below the covering workspace), outside the column that scrolls, and is hidden only while the workspace covers AND is showing", () => {
    // BEFORE: the banner was a floating box with its own layer (z-50) ABOVE the covering workspace, so this pinned that layer and a rule keyed to data-covering alone.
    // That layer no longer exists: the banner was a child of WorldStack, and a rule keyed to data-covering alone hid it while a MINIMIZED workspace (mounted, hidden,
    // still 'covering' by the window's width) left no fixture label anywhere. CANDIDATE 7: the banner is a persistent required notice, so it is PINNED in the strip's footer, OUTSIDE the column
    // and the scroller (a scroll position can no longer carry it out of the window: measured on candidate 6 at a short window). The pin is that structure and that lifecycle, each of which fails if it regresses.
    const css = read('components/diMissionRequest.css');
    expect(css).toMatch(
      /body:has\(\.di-mr\[data-covering='true'\]:not\(\[hidden\]\)\) \.di-fixture-banner\s*\{[^}]*visibility:\s*hidden\s*;/,
    );
    const app = read('App.tsx');
    const open = app.indexOf('<WorldStack>');
    const close = app.indexOf('</WorldStack>');
    const banner = app.indexOf('data-testid="di-fixture-banner"');
    const railAt = app.indexOf('<WorldLeftRail');
    const railEnd = app.indexOf('</WorldLeftRail>');
    expect(open, 'App.tsx renders the world column').toBeGreaterThan(-1);
    expect(close).toBeGreaterThan(open);
    expect(banner, 'App.tsx renders the fixture banner').toBeGreaterThan(-1);
    // BEFORE: the banner was asserted INSIDE the column. NOW the opposite is the point, and it is asserted: the banner is NOT in the column (which scrolls), it is in the strip's notice slot
    expect(
      banner > open && banner < close,
      'the fixture banner is NOT rendered inside the scrolling world column',
    ).toBe(false);
    expect(
      banner > railAt && banner < railEnd,
      "the fixture banner is rendered inside the left strip (its notice slot)",
    ).toBe(true);
    expect(app.slice(railAt, banner), "the banner is passed as the strip's notice").toContain('notice=');
    const rail = read('components/WorldLeftRail.tsx');
    const scroller = rail.indexOf('data-testid="di-world-rail"');
    const footer = rail.indexOf('data-testid="di-world-rail-footer"');
    const noticeAt = rail.indexOf('{notice}');
    expect(scroller, 'the strip renders its scroller').toBeGreaterThan(-1);
    expect(footer, 'the strip renders a pinned footer after the scroller').toBeGreaterThan(scroller);
    expect(noticeAt, 'the notice is rendered in the footer, after the scroller').toBeGreaterThan(footer);
    expect(
      rail.slice(rail.lastIndexOf('<div', scroller), scroller),
      'the scroller is not a tab stop (tabIndex -1)',
    ).toContain('tabIndex={-1}');
    const tag = app.slice(app.lastIndexOf('<div', banner), banner);
    expect(tag, 'the banner element').toContain('di-fixture-banner');
    expect(tag, 'the banner states no layer of its own').not.toMatch(/\bz-(\[\d+\]|\d+)/);
    // the layer it is in: the left strip's (the column sits in row 2 of it), which the table above pins below the covering workspace. BEFORE this named WorldStack.tsx as the file
    // stating layer 30; the column states none of its own now (nor do the camera-view controls), so a layer re-added to either would be unclassified and fail the completeness check.
    expect(layersIn('components/WorldLeftRail.tsx')).toContain(30);
    expect(layersIn('components/WorldStack.tsx'), 'the column states no layer of its own').toEqual([]);
    expect(layersIn('components/WorldViewControls.tsx'), 'the camera-view controls state no layer of their own').toEqual([]);
    const stackAt = app.indexOf('<WorldStack>');
    expect(railAt > -1 && stackAt > railAt && stackAt < railEnd, 'the column is rendered INSIDE the left strip').toBe(true);
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
          unclassified.push(
            `${file}: z ${z}: decide whether it sits BELOW the covering workspace (add it to WORLD_OVERLAYS, at most ${COVERING - 1}) or ABOVE it (add it to ABOVE_THE_WORKSPACE, at least ${COVERING + 1})`,
          );
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
