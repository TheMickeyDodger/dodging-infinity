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
  // The scan click-in stage (mission, diff, Herdr, comments) covers the world the way the left strip does,
  // and it stays below the covering workspace.
  // 30 is the stage inside the dock. 43 is the dock itself while Mission details (or a nested
  // screen) is open, so it covers agent labels (41/42) and stays under the covering workspace (44).
  { file: 'components/diScanPanel.css', what: 'the scan click-in stage (and, in one pane while a screen is open, the minimized-surface strip: 43, later in the document than the dock)', z: [30, 43] },
  // Deliverable 3: fixture mode's in-panel label on the scan face, its own strip across the top of the window, one above the click-in stage (30) so the
  // stage never paints over it. It is INSIDE the scan panel's own layer (the dock, 20, or 43 while a stage is open), so it sits BELOW the covering
  // workspace, which carries its own fixture label.
  { file: 'components/diScanPanel.css', what: "the scan face's fixture label strip", z: [31] },
  // Pass C item 5: the scan face's world-controls disclosure (the chevron at the world's top-right). DECIDED to sit BELOW the
  // covering workspace: it is a world control like the left strip, one above that strip (30) so the strip never paints over it,
  // and under the click-in stage while open (43), which covers the world it belongs to.
  { file: 'index.css', what: "the world's controls disclosure (scan face)", z: [31] },
];

/** The intro bubble, modals, notices and dialog backdrops: they must sit ABOVE the covering workspace. */
const ABOVE_THE_WORKSPACE: Layer[] = [
  { file: 'constants.ts', what: "the greeter's intro bubble (tour and consent)", z: [46] },
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
/** The workspace beside the panel while a scan screen is open (the Close repair): it must sit above the open scan stage. */
const BESIDE_SCAN_OPEN = ruleLayer("body:has(.di-scan[data-stage='open']) .di-mr:not([data-covering='true'])");
/** The scan panel's layer while a screen is open, read from where it is stated. */
const SCAN_STAGE_OPEN = (() => {
  const rule = /(?:^|\n)\.di-scan\[data-stage='open'\] \{([\s\S]*?)\n\}/.exec(read('components/diScanPanel.css'));
  const z = rule ? /z-index:\s*(\d+)\s*;/.exec(rule[1]) : null;
  if (!z) throw new Error("the rule .di-scan[data-stage='open'] states no z-index");
  return Number(z[1]);
})();
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

  it('on the scan face, while a screen is open, the workspace beside takes the covering layer, ABOVE the open scan stage, so its controls stay reachable by pointer', () => {
    // the open stage spans the window from the rail's edge; under it, "Close requests" could not be clicked at 1440 x 900 (diScanRequestCloseBrowser.test.ts)
    expect(BESIDE_SCAN_OPEN).toBe(COVERING);
    expect(BESIDE_SCAN_OPEN).toBeGreaterThan(SCAN_STAGE_OPEN);
  });

  it('on the scan face, while a ONE-PANE screen is open, the minimized-surface strip takes the open screen\'s layer, later in the document than the dock so ABOVE it, and stays BELOW the covering workspace: its Restore controls stay reachable (B3; extends the strip\'s pin to the case it did not consider)', () => {
    const css = read('components/diScanPanel.css');
    const rule = /body:has\(\.di-scan\[data-stage='open'\]\[data-two-pane='false'\]:not\(\[hidden\]\)\) \.di-min-tray \{([\s\S]*?)\n\}/.exec(css);
    expect(rule, 'the one-pane strip rule exists').not.toBeNull();
    const z = /z-index:\s*(\d+)\s*;/.exec(rule![1]);
    expect(z, 'it states a layer').not.toBeNull();
    expect(Number(z![1])).toBe(SCAN_STAGE_OPEN);
    expect(Number(z![1])).toBeLessThan(COVERING);
    // equal layers paint in document order: the strip is rendered AFTER the office panel, so it is ABOVE the dock's layer
    const app = read('App.tsx');
    expect(app.indexOf('data-testid="di-minimized-tray"')).toBeGreaterThan(app.indexOf('<DiOfficePanel'));
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
      if (file === 'components/diMissionRequest.css') continue; // the workspace's own layers (beside, covering, and beside while a scan screen is open), read above by selector
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

  // The anchored popover on the scan face (B2c; RESTATED for the Options menu, 1B/B3). Its own layer is ONE, 1 (diPopover.css), and LOCAL: it lifts the popover over the positioned content that FOLLOWS it
  // inside the stacking context it sits in, and cannot reach between the window's groups. There are THREE popovers in TWO containers, and WHICH context holds the popover differs, so what must hold differs:
  //   THE STAGE (`.di-scan-stage`: position fixed, layer 30): the Code Diff "More" and the Mission details "More actions". The stage is the context. It is a FIXED child of the dock, so its popovers are not
  //     cut by the dock's overflow (measured: the Code Diff menu is drawn 255 px outside the dock's box, in the stage's own column) and reach the window's layers only as the DOCK: 20, or 43 while a screen is open.
  //   THE MISSION-LIST OFFICE (the dock `.di-scan` itself): the list's "Options". NO stage between: the list wrapper, the header and its tools state no layer, position or other context-making property, so the
  //     DOCK is the context, and the dock's own `overflow: hidden` bounds the popover to the dock's box. Inside the dock the positioned things that could paint over it are the rail (1, EARLIER in the document, so below
  //     an equal layer) and the stage (30); the stage cannot hide an open Options popover because the list is `visibility: hidden` while a screen covers it (one pane), and beside the list at two panes (B2b).
  //   The window's layers then bound all three: the dock closed (20) is BELOW the workspace beside it (21); while a screen is open the dock (43) is BELOW the workspace, which takes the covering layer (44) and so paints
  //   over the dock and every popover in it. A new anchored popover on the scan face fails by NAME until it is classified here; it is never a count.
  const SCAN_POPOVERS = [
    { testid: 'di-scan-diff-more', container: 'stage' },
    { testid: 'di-scan-more-actions', container: 'stage' },
    { testid: 'di-scan-options', container: 'office' },
  ] as const;
  /** Every `<AnchoredPopover ...>` opening tag in a source (brace- and quote-aware, so a `=>` or a template literal inside a prop does not end the tag), with the testid the tag states. */
  function popoverTags(source: string): Array<{ testid: string | null; at: number }> {
    const found: Array<{ testid: string | null; at: number }> = [];
    for (const m of source.matchAll(/<AnchoredPopover\b/g)) {
      let depth = 0;
      let quote: string | null = null;
      let i = m.index + m[0].length;
      for (; i < source.length; i += 1) {
        const c = source[i]!;
        if (quote !== null) {
          if (c === quote && source[i - 1] !== '\\') quote = null;
          continue;
        }
        if (c === '"' || c === "'" || c === '`') quote = c;
        else if (c === '{') depth += 1;
        else if (c === '}') depth -= 1;
        else if (c === '>' && depth === 0) break;
      }
      found.push({ testid: /\btestid="([^"]+)"/.exec(source.slice(m.index, i))?.[1] ?? null, at: m.index });
    }
    return found;
  }
  /** The declaration text of EVERY rule whose selector list contains exactly this selector (one rule per block; at-rules are looked through). */
  function declarationsOf(css: string, selector: string): string {
    const out: string[] = [];
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1]!.split(',').some((part) => part.trim() === selector)) out.push(m[2]!);
    return out.join('\n');
  }
  /** Properties that make an element a stacking context or a clipping box of its own. */
  const MAKES_A_CONTEXT = /(?:^|[;\s])(?:position|z-index|transform|filter|backdrop-filter|perspective|opacity|will-change|contain|isolation|mix-blend-mode|translate|rotate|scale|clip-path|mask|overflow)\s*:/;

  it('the anchored popover states ONE layer, 1, and it is LOCAL: never portalled out of the container it is drawn in', () => {
    expect(layersIn('components/diPopover.css')).toEqual([1]);
    expect(1).toBeLessThanOrEqual(BESIDE);
    expect(withoutComments(read('components/ui/AnchoredPopover.tsx')), 'drawn where its trigger is, never portalled out').not.toMatch(/createPortal/);
    expect(declarationsOf(withoutComments(read('components/diPopover.css')), '.di-popover-anchor'), 'the anchor adds no layer of its own').not.toMatch(/z-index/);
  });

  it('every anchored popover on the scan face is classified by NAME in one of two containers (a new one fails here until it is; no count to bump)', () => {
    const found = popoverTags(withoutComments(read('components/DiScanPanel.tsx')));
    expect(found.filter((t) => t.testid === null).length, 'every popover states a testid, or it cannot be classified').toBe(0);
    expect(found.map((t) => t.testid).sort()).toEqual(SCAN_POPOVERS.map((p) => p.testid).sort());
  });

  it('THE STAGE: its two popovers are drawn inside the stage, a fixed element that states its own layer (30), so each is local to the stage (the window sees it only as the dock, bounded in the last test below)', () => {
    const panel = withoutComments(read('components/DiScanPanel.tsx'));
    const css = withoutComments(read('components/diScanPanel.css'));
    const stageAt = panel.indexOf('function Stage(');
    const nextFn = panel.indexOf('\nfunction ', stageAt + 1);
    const nextExport = panel.indexOf('\nexport function ', stageAt + 1);
    const stageEnd = Math.min(...[nextFn, nextExport].filter((i) => i > -1));
    expect(stageAt > -1 && stageEnd > stageAt, 'the Stage function is found').toBe(true);
    const tags = popoverTags(panel);
    for (const use of SCAN_POPOVERS.filter((p) => p.container === 'stage')) {
      const at = tags.find((t) => t.testid === use.testid)?.at ?? -1;
      expect(at > stageAt && at < stageEnd, `${use.testid} is drawn inside the Stage`).toBe(true);
    }
    const stage = declarationsOf(css, '.di-scan-stage');
    expect(stage, 'the stage is positioned, so it is its own containing block (its popovers are not cut by the dock)').toMatch(/position:\s*fixed;/);
    expect(stage, "the stage states its own layer, so it is its popovers' stacking context").toMatch(/z-index:\s*30;/);
  });

  it('THE MISSION-LIST OFFICE: its Options popover has NO stage and no context-making ancestor between it and the dock, so the dock (20; 43 while a screen is open) is its context and bounds it', () => {
    const panel = withoutComments(read('components/DiScanPanel.tsx'));
    const css = withoutComments(read('components/diScanPanel.css'));
    const stageAt = panel.indexOf('function Stage(');
    const nextFn = panel.indexOf('\nfunction ', stageAt + 1);
    const nextExport = panel.indexOf('\nexport function ', stageAt + 1);
    const stageEnd = Math.min(...[nextFn, nextExport].filter((i) => i > -1));
    const homeAt = panel.indexOf('className="di-scan-home"');
    const at = popoverTags(panel).find((t) => t.testid === 'di-scan-options')?.at ?? -1;
    expect(homeAt > -1 && at > homeAt, 'the Options popover is drawn inside the Mission list (the home wrapper)').toBe(true);
    expect(at < stageAt || at > stageEnd, 'and NOT inside the Stage').toBe(true);
    // between the popover and the dock: the list wrapper, the header and its tools, and the popover anchor state no layer, position or clipping box of their own
    for (const selector of ['.di-scan-home', '.di-scan-header', '.di-scan-header-tools'])
      expect(declarationsOf(css, selector), `${selector} makes no stacking context or clipping box of its own`).not.toMatch(MAKES_A_CONTEXT);
    // the dock is the context, states its layer, and bounds what is drawn in it
    const dock = declarationsOf(css, '.di-scan');
    expect(dock, 'the dock is positioned').toMatch(/position:\s*fixed;/);
    expect(dock, 'the dock states its closed layer').toMatch(/z-index:\s*20;/);
    expect(dock, "the dock bounds what is drawn in it (the popover's frame keeps the menu inside)").toMatch(/overflow:\s*hidden;/);
    expect(declarationsOf(css, ".di-scan[data-stage='open']"), 'the dock takes the open screen\'s layer while a screen is open').toMatch(new RegExp(`z-index:\\s*${SCAN_STAGE_OPEN};`));
    // what could paint over it inside the dock: the rail (an EQUAL layer EARLIER in the document, so below it) and the stage (the list is hidden while a screen covers it)
    expect(declarationsOf(css, '.di-rail')).toMatch(/z-index:\s*1;/);
    expect(panel.indexOf('<Rail'), 'the rail is rendered BEFORE the list, so at an equal layer the popover is above it').toBeGreaterThan(-1);
    expect(panel.indexOf('<Rail')).toBeLessThan(homeAt);
    expect(declarationsOf(css, ".di-scan-home[data-covered='true']"), 'a covered list, and so its open popover, is not drawn under the stage').toMatch(/visibility:\s*hidden;/);
    expect(panel, 'the list is covered exactly when a screen is open on ONE pane').toContain("data-covered={view === 'list' || twoPane ? undefined : 'true'}");
  });

  it('THE WINDOW BOUNDS BOTH CONTAINERS: the dock is below the workspace beside it, and the dock open on a screen (43) is below the workspace that then takes the covering layer', () => {
    const dockClosed = Number(/z-index:\s*(\d+);/.exec(declarationsOf(withoutComments(read('components/diScanPanel.css')), '.di-scan'))?.[1]);
    expect(dockClosed, "the dock's closed layer is below the workspace beside it, so no popover in it can paint over the workspace").toBeLessThan(BESIDE);
    expect(SCAN_STAGE_OPEN, "the dock's layer while a screen is open is below the workspace's covering layer").toBeLessThan(COVERING);
    expect(BESIDE_SCAN_OPEN, 'and the workspace beside an open screen takes that covering layer, ABOVE the dock and every popover in it').toBeGreaterThan(SCAN_STAGE_OPEN);
  });

  it('every classified layer is still stated where it was (the table is not stale)', () => {
    for (const layer of [...WORLD_OVERLAYS, ...ABOVE_THE_WORKSPACE]) {
      const stated = new Set(layersIn(layer.file));
      for (const z of layer.z)
        expect(stated.has(z), `${layer.what}: ${layer.file} no longer states z ${z}`).toBe(true);
    }
  });
});
