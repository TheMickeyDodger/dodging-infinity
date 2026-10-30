/**
 * Stage 4, header-overlap correction: the scan stage's HEADER must never draw one item over another, clip an item's own text, push an item off the bar, hide
 * or omit a control, or lose the page from the Location navigation, at ANY width of the desktop matrix (900 to 1672), for ANY of the four second-crumb labels
 * (Open, Merged, Closed, Archived), on Code Diff AND Comments, on the SPECIMEN AND the REAL App.
 *
 * What this pins, and what it does not claim:
 *  - It is a REGRESSION for a defect that was found by measurement (the last crumb ran into the tab row below about 1046 px; 145.7 px at 900 on the specimen,
 *    100.7 px on the real App with the Open label, 128 with Archived). It was written FIRST and was red on the unfixed product, for that reason.
 *  - COMMENTS was believed to share the defect (a shared-component inference). Measured: it does not occur (0 of 96 cases). The Comments cases here keep it an
 *    observation.
 *  - The rule must hold if the header's sizes and spacing CHANGE (for instance when the locked geometry is restored), so the matrix is also run under two INJECTED
 *    ROBUSTNESS FIXTURES (see ROBUSTNESS_FIXTURE_B and _C below). They are NOT the reference and their numbers are NOT a target: B is an unrealistic upper bound, C is
 *    derived from the stylesheet's own 0.72 convention (no reference exists below 1500). Passing them is evidence that the RULE is geometry-independent. Conformance
 *    to the locked design is verified ELSEWHERE, like for like against the actual reference at 1672.
 *  - It does NOT restore the locked geometry (mark, divider, tool pitch and size, crumb gaps): those are recorded, sized, unrestored deviations.
 *
 * The instrument (test/diHeaderRegion.ts) measures boxes AND pixels and carries its own controls; they run FIRST, and nothing below is trusted unless they pass.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, test } from 'vitest';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import type { HeaderMeasure } from './diHeaderRegion.ts';
import { drawnOverlaps, measureHeader, runHeaderControls, tabsNotReachable } from './diHeaderRegion.ts';

const STEPS_MS = 240_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: { live: DiOfficeState };
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

/** The four second-crumb labels (`DI_SCAN_TABS`): the label moves the onset, so the axis is ENUMERATED, not sampled. */
const LABELS = ['Open', 'Merged', 'Closed', 'Archived'] as const;
/** Every 40 px from 900 to 1260, then the locked widths (1280, 1672) and the compact-scale break (1480, 1500). */
const WIDTHS = [900, 940, 980, 1020, 1060, 1100, 1140, 1180, 1220, 1260, 1280, 1480, 1500, 1672];
const PIXEL_WIDTHS = new Set([900, 980, 1060, 1140, 1280]);
const heightFor = (w: number) => (w >= 1500 ? 941 : 720);
const LONG_TITLE = 'A very long Mission title that goes on and on well past any column '.repeat(2);

/*
 * ROBUSTNESS FIXTURES, NOT THE REFERENCE. The two stylesheets below are INJECTED to prove that the responsive rule is GEOMETRY-INDEPENDENT: if the header's sizes and
 * spacing change (for instance when the locked geometry is restored), the rule must still prevent every overlap. Their numbers are NOT the locked design and must never
 * be read as a target:
 *  - FIXTURE B applies the pixel-lock's 1672 ink values (mark 55 x 36, tool gap 38, crumb gap 18) at EVERY width. That is an upper bound and deliberately unrealistic; as a
 *    robustness fixture an unrealistic geometry is a FEATURE (a rule that survives it survives anything), as a reference it would simply be wrong.
 *  - FIXTURE C is DERIVED from the stylesheet's own convention of about 0.72 of the pixel-lock scale below 1500 px (mark 40 x 26, lead gap 16, crumb gap 13, tool gap 18).
 *    No reference exists below 1500 px; C is an approximation used to SCREEN, not a measurement.
 * CONFORMANCE TO THE LOCKED DESIGN IS NOT VERIFIED HERE. It is verified elsewhere, like for like (ink against ink), against the actual reference at 1672 x 941
 * (owner-review-references/pixelDiff.png and pixelDetails.png, which disagree with each other on the mark: see reports/geometry-restoration-handoff-PREP.md). Nobody
 * restoring the header's size or spacing should "correct" the product to a number in this file.
 */
const ROBUSTNESS_FIXTURE_B = `
.di-scan-mark svg { width: 55px; height: 36px; }
.di-scan-header-tools { gap: 38px; }
.di-scan-crumb { gap: 18px; }`;
const ROBUSTNESS_FIXTURE_C = `
.di-scan-stage-bar .di-scan-mark svg { width: 40px; height: 26px; }
.di-scan-stage-bar .di-scan-header-lead { gap: 16px; }
.di-scan-stage-bar .di-scan-crumb { gap: 13px; margin-left: 15px; }
.di-scan-stage-bar .di-scan-header-tools { gap: 18px; }`;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let port = 0;
let unavailable: string | null = null;
let socket: WebSocket | null = null;
let onReady: (ws: WebSocket) => void = () => undefined;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
  await server.listen();
  wss = new WebSocketServer({ noServer: true });
  server.httpServer!.on('upgrade', (req, sock, head) => {
    if (!req.url?.startsWith('/ws')) {
      sock.destroy();
      return;
    }
    wss!.handleUpgrade(req, sock, head, (ws) =>
      ws.on('message', (data) => {
        const msg = JSON.parse(String(data)) as Record<string, unknown>;
        if (msg.type === 'webviewReady') {
          socket = ws;
          onReady(ws);
        } else if (msg.type === 'diCodeReviewRead') {
          const stored = msg.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === msg.fileId);
          if (stored === undefined) return;
          const reply = structuredClone(stored);
          if (typeof msg.requestId === 'string') reply.requestId = msg.requestId;
          ws.send(JSON.stringify(reply));
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, STEPS_MS);

afterAll(async () => {
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

type Surface = 'specimen' | 'real';
type View = 'diff' | 'comments';

/** Open one surface on one view. The SPECIMEN is the scan harness over labelled sample data; the REAL App is fed over a real WebSocket by the in-process office. */
async function openSurface(surface: Surface, view: View, width: number): Promise<Page> {
  const page = await browser!.newPage({ viewport: { width, height: heightFor(width) } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  if (surface === 'specimen') {
    await page.goto(`http://localhost:${port}/test/harness/di-scan-lock.html?di-scan-view=${view}`, { waitUntil: 'domcontentloaded' });
  } else {
    const ready = new Promise<WebSocket>((resolve) => (onReady = resolve));
    await page.goto(`http://localhost:${port}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
    await ready;
    socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
    socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
    socket!.send(JSON.stringify(doc.states.live));
    await page.waitForSelector('[data-testid="di-rail-orchestrator"]');
    await page.click('[data-testid="di-rail-orchestrator"] >> nth=0');
    await page.waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await page.click('[data-testid="di-scan-code-diff"]');
    await page.waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
    if (view === 'comments') {
      await page.click('[data-testid="di-scan-sibling-comments"]');
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="comments"]');
    }
  }
  await page.waitForSelector('.di-scan-stage-bar');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return page;
}

/** Set the viewport, the second crumb's label and (optionally) the title: SYNTHETIC variations of the page's own text, applied to the live DOM. */
async function shape(page: Page, width: number, label: string, title: string | null): Promise<void> {
  await page.setViewportSize({ width, height: heightFor(width) });
  await page.evaluate(
    ({ label, title }) => {
      // Comments is drawn to its own reference (v3 07) and has NO crumb: there is no label or title to vary there
      const crumb = document.querySelector('.di-scan-crumb');
      if (crumb === null) return;
      const kids = [...crumb.children] as HTMLElement[];
      kids[2]!.textContent = label;
      if (title !== null) kids[4]!.textContent = title;
    },
    { label, title },
  );
  await page.waitForTimeout(60);
}

const leafOf = (view: View) => (view === 'diff' ? 'Code Diff' : 'Comments');

/** Everything that must hold at ANY width, label and surface; returns the violations (empty = clean). */
function violations(m: HeaderMeasure, tag: string, view: View, label: string, expectedOrder: string[] | null): string[] {
  const out: string[] = [];
  if (m.overlaps.length) out.push(`${tag}: OVERLAP ${m.overlaps.map((o) => `${o.a} x ${o.b}: ${o.px}px`).join(', ')}`);
  if (m.clipped.length) out.push(`${tag}: CLIPPED own text ${m.clipped.join(',')}`);
  if (m.offBar.length) out.push(`${tag}: OFF THE BAR ${m.offBar.join(',')}`);
  if (m.omitted.length) out.push(`${tag}: OMITTED (not rendered) ${m.omitted.join(',')}`);
  if (m.visuallyHidden.length) out.push(`${tag}: VISUALLY HIDDEN ${m.visuallyHidden.join(',')}`);
  if (m.docScrollW > m.innerW) out.push(`${tag}: the page scrolls sideways (${m.docScrollW} > ${m.innerW})`);
  const tree = m.locationAria;
  if (view === 'comments') {
    // Comments is reconciled to v3 07, which draws no crumb: there must be NO Location landmark and no crumb item here (was: the landmark kept World, the label and the leaf)
    if (tree !== '') out.push(`${tag}: Comments has a Location landmark, which its reference does not draw: ${tree.replace(/\s+/g, ' ').slice(0, 120)}`);
  } else {
    for (const name of ['World', label, leafOf(view)]) if (!tree.includes(name)) out.push(`${tag}: the Location landmark lost "${name}": ${tree.replace(/\s+/g, ' ').slice(0, 160)}`);
  }
  // the REQUIRED sibling tabs, collected by test id whatever their box (the item list cannot report an omitted tab)
  const lost = tabsNotReachable(m);
  if (lost.length) out.push(`${tag}: required sibling tab(s) not reachable: ${lost.join(',')}`);
  if (expectedOrder !== null && JSON.stringify(m.domOrder) !== JSON.stringify(expectedOrder)) out.push(`${tag}: header control order changed: ${m.domOrder.join(' > ')}`);
  return out;
}

async function sweep(surface: Surface, view: View, geometry: string | null, labels: readonly string[], widths: number[], titles: Array<string | null> = [null]): Promise<{ problems: string[]; cases: number; wrapped: number }> {
  const page = await openSurface(surface, view, widths[0]!);
  if (geometry !== null) await page.addStyleTag({ content: geometry });
  const problems: string[] = [];
  let cases = 0;
  let wrapped = 0;
  let order: string[] | null = null;
  try {
    for (const title of titles) {
      for (const label of labels) {
        for (const width of widths) {
          await shape(page, width, label, title);
          const m = await measureHeader(page);
          const tag = `${surface}/${view}/${label}/${title === null ? 'own' : `t${title.length}`}/${width}${geometry === null ? '' : '/injected'}`;
          if (order === null) order = m.domOrder;
          // the control order is the SAME at every width; the second crumb's name is the label and the title button's name is the title, both of which this sweep sets
          const expected = view === 'comments' ? order : order.map((n, i) => (i === 2 ? label : i === 3 && title !== null ? title.trim() : n));
          problems.push(...violations(m, tag, view, label, expected));
          if (m.rows > 1) wrapped++;
          if (view === 'comments' && m.rows !== 1) problems.push(`${tag}: Comments wrapped (rows ${m.rows}): the wrap is scoped to Code Diff`);
          if (width >= 1280 && m.rows !== 1) problems.push(`${tag}: a locked or wider width is not ONE row (rows ${m.rows})`);
          if (geometry === null && PIXEL_WIDTHS.has(width) && title === null) {
            const px = await drawnOverlaps(page);
            if (px.overdrawPairs.length) problems.push(`${tag}: PIXELS drawn over each other ${px.overdrawPairs.map((o) => `${o.a} x ${o.b}: ${o.sharedDrawnPixels}px`).join(', ')}`);
          }
          cases++;
        }
      }
    }
  } finally {
    await page.close();
  }
  return { problems, cases, wrapped };
}

const skipIfUnavailable = (): boolean => {
  if (unavailable) console.warn(`SKIPPED (header regression): ${unavailable}`);
  return unavailable !== null;
};

test(
  'THE INSTRUMENT\'S OWN CONTROLS read as expected (negative and positive, for clipped text, hidden text, box overlap, drawn overlap, omission and the Location tree) before anything below is trusted',
  async () => {
    if (skipIfUnavailable()) return;
    const results = await runHeaderControls(browser!);
    const failed = results.filter((r) => !r.ok);
    assert.equal(failed.length, 0, `controls that read wrong: ${failed.map((f) => `${f.control} ${f.why}`).join(' | ')}`);
    assert.ok(results.length >= 15, `all the controls ran (${results.length})`);
  },
  STEPS_MS,
);

for (const surface of ['specimen', 'real'] as const) {
  for (const view of ['diff', 'comments'] as const) {
    test(
      view === 'diff'
        ? `CODE DIFF on the ${surface.toUpperCase()}: no overlap, no clipped text, nothing off the bar, nothing omitted or hidden, the Location landmark and the control order intact, by BOXES and by PIXELS, for all four labels at every width of the matrix`
        : `COMMENTS on the ${surface.toUpperCase()}: no overlap, no clipped text, nothing off the bar, nothing omitted or hidden, NO Location landmark and no crumb item (Comments draws none), the required sibling tabs reachable, the control order intact and ONE row (no wrap), by BOXES and by PIXELS, for its one case (the label Open) at every width of the matrix`,
      async () => {
        if (skipIfUnavailable()) return;
        // Comments has no crumb, so no second-crumb label to vary: one case per width (Code Diff keeps all four labels)
        const labels = view === 'comments' ? (['Open'] as const) : LABELS;
        const r = await sweep(surface, view, null, labels, WIDTHS);
        assert.deepEqual(r.problems, [], `${r.problems.length} violation(s) in ${r.cases} cases:\n${r.problems.slice(0, 24).join('\n')}`);
        assert.equal(r.cases, labels.length * WIDTHS.length);
      },
      STEPS_MS,
    );
  }
}

test(
  'THE TITLE axis (the page\'s own, one letter, 134 characters): the title is the flexible item, so nothing overlaps for any title, and a one-letter title is never truncated',
  async () => {
    if (skipIfUnavailable()) return;
    for (const surface of ['specimen', 'real'] as const) {
      const r = await sweep(surface, 'diff', null, ['Open', 'Archived'], [900, 960, 1020, 1100, 1180, 1280], [null, 'A', LONG_TITLE]);
      assert.deepEqual(r.problems, [], `${surface}: ${r.problems.length} violation(s):\n${r.problems.slice(0, 20).join('\n')}`);
      const page = await openSurface(surface, 'diff', 900);
      try {
        for (const width of [900, 960, 1020, 1100, 1280]) {
          await shape(page, width, 'Archived', 'A');
          const truncated = await page.evaluate(() => {
            const t = document.querySelector('.di-scan-crumb-title') as HTMLElement;
            return t.scrollWidth > t.clientWidth + 1;
          });
          assert.equal(truncated, false, `${surface}/${width}: a ONE-LETTER title is truncated (it has no room because the header did not give the crumb its own row)`);
        }
      } finally {
        await page.close();
      }
    }
  },
  STEPS_MS,
);

test(
  'THE ONE-LETTER TITLE WINDOW (a fine sweep, every 8 px from 900 to 1300): a one-letter title is never truncated at ANY of 51 widths, so a window of about one letter wide cannot sit between two samples (named finding F-TITLE-1: the title track had a minimum of 0, and a 40 px sweep concealed the window)',
  async () => {
    if (skipIfUnavailable()) return;
    const widths = Array.from({ length: 51 }, (_, i) => 900 + i * 8);
    const bad: string[] = [];
    for (const surface of ['specimen', 'real'] as const) {
      const page = await openSurface(surface, 'diff', 900);
      try {
        for (const label of ['Open', 'Archived']) {
          for (const width of widths) {
            await shape(page, width, label, 'A');
            const truncated = await page.evaluate(() => {
              const t = document.querySelector('.di-scan-crumb-title') as HTMLElement;
              return t.scrollWidth > t.clientWidth + 1;
            });
            if (truncated) bad.push(`${surface}/${label}/${width}`);
          }
        }
      } finally {
        await page.close();
      }
    }
    assert.deepEqual(bad, [], `a ONE-LETTER title is truncated at ${bad.length} of ${widths.length * 4} sampled widths: ${bad.slice(0, 12).join(', ')}`);
  },
  STEPS_MS,
);

test(
  'COMPOSITION: where the header takes a second row, the crumb keeps its own row (back, mark, crumb) and the TABS AND TOOLS wrap TOGETHER on the row below, right-aligned; nothing else moves',
  async () => {
    if (skipIfUnavailable()) return;
    const problems: string[] = [];
    let wrappedCases = 0;
    for (const surface of ['specimen', 'real'] as const) {
      const page = await openSurface(surface, 'diff', 900);
      try {
        for (const label of ['Open', 'Archived']) {
          for (const width of [900, 960, 1020, 1060, 1100]) {
            await shape(page, width, label, null);
            const m = await measureHeader(page);
            if (m.rows < 2) continue;
            wrappedCases++;
            const tag = `${surface}/${label}/${width}`;
            const centre = (b: { y: number; h: number }) => b.y + b.h / 2;
            const upper = m.boxes.filter((b) => b.name === 'back' || b.name === 'mark' || b.name.startsWith('crumb') || b.name.startsWith('sep'));
            const lower = m.boxes.filter((b) => b.name.startsWith('tab-') || b.name.startsWith('tool-'));
            if (m.rows !== 2) problems.push(`${tag}: ${m.rows} rows, expected 2`);
            const upperY = Math.max(...upper.map(centre));
            const lowerY = Math.min(...lower.map(centre));
            if (!(lowerY > upperY + 8)) problems.push(`${tag}: the tabs and tools are not wholly below the crumb row (${lowerY.toFixed(0)} vs ${upperY.toFixed(0)})`);
            if (Math.max(...lower.map(centre)) - Math.min(...lower.map(centre)) > 8) problems.push(`${tag}: the tabs and the tools are not on ONE row together`);
            const barRight = await page.evaluate(() => {
              const bar = document.querySelector('.di-scan-stage-bar')!;
              return bar.getBoundingClientRect().right - parseFloat(getComputedStyle(bar).paddingRight);
            });
            const rightmost = Math.max(...lower.map((b) => b.right));
            if (Math.abs(rightmost - barRight) > 1.5) problems.push(`${tag}: the second row is not right-aligned (${rightmost.toFixed(1)} vs ${barRight.toFixed(1)})`);
          }
        }
      } finally {
        await page.close();
      }
    }
    assert.ok(wrappedCases > 0, 'the header wrapped somewhere in the band (otherwise the composition was not exercised)');
    assert.deepEqual(problems, [], problems.join('\n'));
  },
  STEPS_MS,
);

test(
  'NOTHING IS HIDDEN, THE PANE THE ROW TAKES SPACE FROM STILL REACHES ITS CONTENT: at 900 x 720 with the widest label the nested editor ends inside the window, is still tall enough to read, and its first line is the topmost thing drawn 40 px in from its left edge, at mid-height (an element hit test, not a real click)',
  async () => {
    if (skipIfUnavailable()) return;
    for (const surface of ['specimen', 'real'] as const) {
      const page = await openSurface(surface, 'diff', 900);
      try {
        if (surface === 'real') {
          // a TEXT file that has lines (the first tree entry may be a binary or withheld file with none): selected by its file id, as the other real-App tests do
          const textFile = doc.replies.overview.overview!.files.find((f) => f.path === 'src/app.py')!;
          await page.click(`[data-testid="di-scan-tree-file"][data-file-id="${textFile.fileId}"]`);
          await page.waitForSelector('.di-scan-diff-line', { timeout: 15_000 });
        }
        await shape(page, 900, 'Archived', null);
        const reach = await page.evaluate(() => {
          const ide = document.querySelector('.di-scan-ide') as HTMLElement | null;
          const main = document.querySelector('.di-scan-ide-main') as HTMLElement | null;
          const line = document.querySelector('.di-scan-diff-line') as HTMLElement | null;
          if (!ide || !main || !line) return { error: `missing: ide ${!!ide} main ${!!main} line ${!!line}` };
          const mb = main.getBoundingClientRect();
          const lb = line.getBoundingClientRect();
          const top = document.elementFromPoint(lb.x + Math.min(40, lb.width / 2), lb.y + lb.height / 2);
          return {
            error: null,
            ideBottom: ide.getBoundingClientRect().bottom,
            innerH: window.innerHeight,
            mainH: mb.height,
            lineTop: lb.top,
            mainTop: mb.top,
            firstLineIsTopmost: top !== null && (top === line || line.contains(top) || top.closest('.di-scan-diff-line') === line),
          };
        });
        assert.equal(reach.error, null, `${surface}: ${reach.error}`);
        assert.ok((reach.ideBottom ?? 1e9) <= (reach.innerH ?? 0) + 0.5, `${surface}: the nested editor ends inside the window (${reach.ideBottom} <= ${reach.innerH})`);
        assert.ok((reach.mainH ?? 0) >= 300, `${surface}: the editor is still tall enough to read (${reach.mainH})`);
        assert.ok((reach.lineTop ?? 0) >= (reach.mainTop ?? 0) - 0.5, `${surface}: the first line starts inside the editor`);
        assert.equal(reach.firstLineIsTopmost, true, `${surface}: the first diff line is the topmost thing drawn 40 px in from its left edge, at mid-height (hit test, not a real click)`);
      } finally {
        await page.close();
      }
    }
  },
  STEPS_MS,
);

test(
  'THE RULE IS GEOMETRY-INDEPENDENT (two ROBUSTNESS FIXTURES, not the reference): with the header\'s sizes and spacing injected to an unrealistic upper bound (B) and to a derived compact scale (C), Code Diff still has no overlap, clipping, omission or hiding, for all four labels',
  async () => {
    if (skipIfUnavailable()) return;
    for (const [name, css] of [['B', ROBUSTNESS_FIXTURE_B], ['C', ROBUSTNESS_FIXTURE_C]] as const) {
      for (const surface of ['specimen', 'real'] as const) {
        const r = await sweep(surface, 'diff', css, LABELS, [900, 940, 980, 1020, 1060, 1100, 1140, 1180, 1220, 1260]);
        assert.deepEqual(r.problems, [], `robustness fixture ${name} on the ${surface}: ${r.problems.length} violation(s):\n${r.problems.slice(0, 16).join('\n')}`);
      }
    }
  },
  STEPS_MS,
);

test(
  'THE STRUCTURE THE RULE RESTS ON is pinned: the Code Diff crumb is seven items with the title fifth, and the tabs and the tools are the two children of ONE controls wrapper, in that DOM order',
  async () => {
    if (skipIfUnavailable()) return;
    for (const surface of ['specimen', 'real'] as const) {
      const page = await openSurface(surface, 'diff', 1280);
      try {
        const s = await page.evaluate(() => {
          const crumb = document.querySelector('.di-scan-crumb')!;
          const wrap = document.querySelector('.di-scan-stage-bar > .di-scan-header-controls');
          return {
            crumbItems: crumb.children.length,
            fifthIsTitle: crumb.children[4]?.classList.contains('di-scan-crumb-title') ?? false,
            wrapperChildren: wrap ? [...wrap.children].map((c) => c.className) : null,
            wrapperRoleless: wrap ? !wrap.hasAttribute('role') && !wrap.hasAttribute('tabindex') && !wrap.hasAttribute('aria-label') : false,
          };
        });
        assert.equal(s.crumbItems, 7, `${surface}: the crumb has seven items`);
        assert.equal(s.fifthIsTitle, true, `${surface}: the fifth crumb item is the title`);
        assert.deepEqual(s.wrapperChildren, ['di-scan-siblings', 'di-scan-header-tools'], `${surface}: the tabs and the tools are the wrapper's two children, in DOM order`);
        assert.equal(s.wrapperRoleless, true, `${surface}: the wrapper carries no role, tabindex or label (it is layout, not a control or a landmark)`);
      } finally {
        await page.close();
      }
    }
  },
  STEPS_MS,
);
