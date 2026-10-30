/**
 * Stage 4, per-view stage-header GEOMETRY restoration: each view's header is measured LIKE FOR LIKE (ink against ink) against ITS OWN governing reference, on the
 * SPECIMEN and on the REAL App:
 *  - Code Diff  -> pixel-lock 03 (1672 x 941);
 *  - Mission details -> pixel-lock 02 (1672 x 941);
 *  - Comments   -> v3 07 (1280 x 720).
 * The numbers live in test/fixtures/stage-header-geometry-targets.FIXTURE.json (the LOCK column is what the reference draws, the TARGET column is what the product must
 * measure; they differ only where a recorded decision says so, in the NOTE). Positions are relative to the stage's own left edge. Method, one function on every image EXCEPT the Mission
 * Back box: the ink of an element is the bounding box of the pixels that differ from the most common colour of its rectangle by more than 40 (scratch/hdr/header_elems.mjs).
 * THE MISSION BACK BOX IS NOT INK AGAINST INK. Its top, stage-relative left, width and height are the button's OUTER LAYOUT RECTANGLE (its DOM box) checked against the earlier pass's HAND-MEASURED reference
 * bounds (the fixture's note: left 296, right 342, top 28, bottom 79), and the Mission gap and centring numbers use that same boundary: LAYOUT-BOX GEOMETRY AGAINST HAND-MEASURED REFERENCE BOUNDS. A layout
 * box is satisfied by a ring of zero contrast, so the 1672 Mission tests ALSO assert that the ring is actually PAINTED at that boundary (test/diRingPaint.ts; the rule is derived from the reference first and recorded
 * in the fixture under views.mission.paint; a ring-removed negative control must fail it).
 *
 * What this pins, and what it does NOT claim:
 *  - It pins the RESTORED geometry at the references' own sizes only. Below 1500 px there is no reference for Code Diff and Mission details; their sizes are DERIVED (about 0.72)
 *    and are not asserted here. Comments' reference is measured at 1280.
 *  - It does not compare glyph artwork (the lock draws outline icons; the product keeps its own assets) or typeface widths (Inter, D-2).
 *  - IDENTITY WITH AN OLD CAPTURE IS NOT CONFORMANCE: this test is the conformance evidence; the old captures are not.
 *  - The instrument's own controls (test/diHeaderRegion.ts, now including the REQUIRED-TAB controls) run first; the required sibling tabs are collected independently of
 *    their box, and a view that has them must keep all three reachable.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { PNG } from 'pngjs';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, test } from 'vitest';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { measureHeader, runHeaderControls, tabsNotReachable } from './diHeaderRegion.ts';
import type { Side, SidePeak } from './diRingPaint.ts';
import { ringPeaks, SIDES, sidesBelow, withRingRemoved } from './diRingPaint.ts';

const STEPS_MS = 240_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: { live: DiOfficeState };
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

interface Target {
  id: string;
  lock: number;
  target: number;
  tol: number;
  note?: string;
  /** derived below 1500 px as about 0.72 of the target (the stylesheet's own convention); asserted only by the DERIVED test */
  scales?: boolean;
}
/** The painted-ring rule of the Mission Back box: DERIVED FROM THE REFERENCE by test/diRingPaintReference.test.ts (never from a candidate) and recorded here before any candidate result is used. */
interface PaintRule {
  rule: string;
  fraction: number;
  referenceSha256: string;
  referencePeaks: Record<Side, number>;
  removedPeaks: Record<Side, number>;
  minPeak: number;
}
interface ViewTargets {
  reference: { file: string; sha256: string; size: [number, number]; stageLeftEdge: number };
  targets: Target[];
  paint?: PaintRule;
}
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/stage-header-geometry-targets.FIXTURE.json'), 'utf-8')) as {
  views: Record<'diff' | 'mission' | 'comments', ViewTargets>;
};

type View = 'diff' | 'mission' | 'comments';
type Surface = 'specimen' | 'real';

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

const skipIfUnavailable = (): boolean => {
  if (unavailable !== null) console.warn(`SKIPPED: ${unavailable}`);
  return unavailable !== null;
};

/** Open one view of one surface at the view's REFERENCE size (or at `size`, for the DERIVED test). */
async function openSurface(surface: Surface, view: View, size?: [number, number]): Promise<Page> {
  const [width, height] = size ?? fixture.views[view].reference.size;
  const page = await browser!.newPage({ viewport: { width, height } });
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
    if (view !== 'mission') {
      await page.click('[data-testid="di-scan-code-diff"]');
      await page.waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
      if (view === 'comments') {
        await page.click('[data-testid="di-scan-sibling-comments"]');
        await page.waitForSelector('[data-testid="di-scan-stage"][data-view="comments"]');
      }
    }
  }
  await page.waitForSelector('.di-scan-stage-bar');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  // the reference's second crumb is "Open": show the same label (a synthetic variation of the page's own text) where a crumb exists
  await page.evaluate(() => {
    const kids = document.querySelector('.di-scan-crumb')?.children;
    if (kids !== undefined && kids[2] !== undefined) (kids[2] as HTMLElement).textContent = 'Open';
  });
  await page.waitForTimeout(80);
  return page;
}

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
interface Ink {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  w: number;
  h: number;
}
/**
 * The INK of a rectangle: the bounding box of the pixels that differ from the BACKGROUND by more than `thr`. The background is the bar's own empty colour (`bgOf`), NEVER the
 * rectangle's most common colour: a dense solid shape (the mark is 62 % filled) outnumbers its own background, and the "ink" then becomes everything that is NOT the shape
 * (that read the mark as its rectangle grown by 2 px, and was found by the first run on the restored product). The reference measurements use the most common colour of
 * their rectangle, which is the same thing wherever the shape is sparse (text, outlines); the bar's colour is the same in the reference's bar.
 */
function ink(png: PNG, r: Rect, grow: number, bg: [number, number, number], thr = 40): Ink | null {
  const x0 = Math.max(0, Math.floor(r.x - grow));
  const y0 = Math.max(0, Math.floor(r.y - grow));
  const x1 = Math.min(png.width, Math.ceil(r.x + r.w + grow));
  const y1 = Math.min(png.height, Math.ceil(r.y + r.h + grow));
  const px = (x: number, y: number): [number, number, number] => {
    const i = (y * png.width + x) * 4;
    return [png.data[i]!, png.data[i + 1]!, png.data[i + 2]!];
  };
  let ax = 1e9;
  let bx = -1;
  let ay = 1e9;
  let by = -1;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const [r2, g, b] = px(x, y);
      if (Math.max(Math.abs(r2 - bg[0]), Math.abs(g - bg[1]), Math.abs(b - bg[2])) > thr) {
        ax = Math.min(ax, x);
        bx = Math.max(bx, x);
        ay = Math.min(ay, y);
        by = Math.max(by, y);
      }
    }
  return bx < 0 ? null : { x0: ax, x1: bx, y0: ay, y1: by, w: bx - ax + 1, h: by - ay + 1 };
}

interface Facts {
  L: number;
  W: number;
  bar: Rect;
  back: Rect | null;
  mark: Rect | null;
  markCount: number;
  crumb: Rect | null;
  crumbCount: number;
  crumbItems: Rect[];
  tools: Rect[];
  heading: Rect | null;
  subtitle: Rect | null;
}
async function facts(page: Page): Promise<Facts> {
  return page.evaluate(() => {
    const rc = (el: Element | null): { x: number; y: number; w: number; h: number } | null => {
      if (el === null) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    };
    const textRc = (el: Element | null): { x: number; y: number; w: number; h: number } | null => {
      if (el === null) return null;
      const rg = document.createRange();
      rg.selectNodeContents(el);
      const r = rg.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height };
    };
    const stage = document.querySelector('.di-scan-stage')!;
    const sb = stage.getBoundingClientRect();
    const bar = document.querySelector('.di-scan-stage-bar')!;
    const crumb = bar.querySelector('.di-scan-crumb');
    return {
      L: sb.left + (parseFloat(getComputedStyle(stage).borderLeftWidth) || 0),
      W: window.innerWidth,
      bar: rc(bar)!,
      back: rc(bar.querySelector('.di-scan-back')),
      mark: rc(bar.querySelector('.di-scan-mark svg')),
      markCount: bar.querySelectorAll('.di-scan-mark').length,
      crumb: rc(crumb),
      crumbCount: bar.querySelectorAll('.di-scan-crumb').length,
      crumbItems: crumb === null ? [] : [...crumb.children].map((c) => rc(c)!),
      tools: [...bar.querySelectorAll('.di-scan-header-tools > button')].map((b) => rc(b)!),
      // the heading's and the subtitle's own TEXT (a Range over their contents), never their block boxes: those span the whole width and would take in whatever overlaps it (the tabs)
      heading: textRc(document.querySelector('.di-scan-comments-title')),
      subtitle: textRc(document.querySelector('.di-scan-comments-sub')),
    };
  });
}

/** An element's OUTER LAYOUT RECTANGLE in the Ink shape (x1 and y1 are the last pixel column and row, as the ink function reports them). Used for the Mission Back box ONLY. */
function layoutBox(r: Rect): Ink {
  return { x0: r.x, x1: r.x + r.w - 1, y0: r.y, y1: r.y + r.h - 1, w: r.w, h: r.h };
}

/** The bar's own empty colour: a pixel in its top padding, 30 px inside the stage's left edge. */
function bgOf(png: PNG, f: Facts): [number, number, number] {
  const i = (3 * png.width + Math.round(f.L) + 30) * 4;
  return [png.data[i]!, png.data[i + 1]!, png.data[i + 2]!];
}

/** The painted-ring evidence of the Back box: one viewport screenshot, the box taken from its DOM rectangle, the four sides' peaks, and the same peaks on a copy of the screenshot with the ring removed (the negative control). Mission only. */
async function paintOf(page: Page): Promise<{ peaks: Record<Side, SidePeak>; removed: Record<Side, SidePeak> }> {
  const f = await facts(page);
  if (f.back === null) throw new Error('there is no Back button to measure');
  const png = PNG.sync.read(await page.screenshot({ type: 'png' }));
  const box = { x: f.back.x, y: f.back.y, w: f.back.w, h: f.back.h };
  return { peaks: ringPeaks(png, box), removed: ringPeaks(withRingRemoved(png, box), box) };
}

/** Every measurable id of every view, measured from ONE viewport screenshot and the DOM boxes. NaN = the element is not there. */
async function measure(page: Page, view: View): Promise<Record<string, number>> {
  const f = await facts(page);
  const png = PNG.sync.read(await page.screenshot({ type: 'png' }));
  const out: Record<string, number> = {};
  const set = (id: string, v: number | null | undefined) => {
    out[id] = v === null || v === undefined ? NaN : v;
  };
  const bg = bgOf(png, f);
  // MISSION DETAILS ONLY: the Back box is its OUTER LAYOUT RECTANGLE (layout-box geometry against hand-measured reference bounds, NOT ink against ink); every other view keeps the ink function
  const backInk = f.back === null ? null : view === 'mission' ? layoutBox(f.back) : ink(png, f.back, 2, bg);
  if (view !== 'comments') {
    set('back.top', backInk?.y0);
    set('back.left', backInk === null ? null : backInk.x0 - f.L);
    set('back.w', backInk?.w);
    set('back.h', backInk?.h);
    // the chevron INSIDE the Back box: ink against the button's own fill (sampled 8 px in from its corner), inside the box inset by 6 px, threshold 60 (the outline is excluded)
    if (f.back !== null) {
      const fi = (Math.round(f.back.y + 8) * png.width + Math.round(f.back.x + 8)) * 4;
      const fill: [number, number, number] = [png.data[fi]!, png.data[fi + 1]!, png.data[fi + 2]!];
      const glyph = ink(png, { x: f.back.x + 6, y: f.back.y + 6, w: f.back.w - 12, h: f.back.h - 12 }, 0, fill, 60);
      set('back.glyph.w', glyph?.w);
      set('back.glyph.h', glyph?.h);
    }
    const markInk = f.mark === null ? null : ink(png, f.mark, 2, bg);
    set('mark.w', markInk?.w);
    set('mark.h', markInk?.h);
    set('mark.left', markInk === null ? null : markInk.x0 - f.L);
    if (markInk !== null && backInk !== null) set('mark.centreVsBack', (markInk.y0 + markInk.y1) / 2 - (backInk.y0 + backInk.y1) / 2);
    const items = f.crumbItems.map((r) => ink(png, r, 2, bg));
    set('world.left', items[0] === null || items[0] === undefined ? null : items[0].x0 - f.L);
    set('mark.gapFromBack', markInk === null || backInk === null ? null : markInk.x0 - backInk.x1 - 1);
    set('world.gapFromMark', markInk === null || items[0] === null || items[0] === undefined ? null : items[0].x0 - markInk.x1 - 1);
    for (let k = 0; k < 4; k++) {
      const a = items[k];
      const b = items[k + 1];
      set(`crumb.gap${k + 1}`, a === null || a === undefined || b === null || b === undefined ? null : b.x0 - a.x1 - 1);
    }
    // the divider: ink in the OPEN GAP between the mark's ink and the first crumb item's ink (stopping 2 px before the text, so no letter is ever measured), above the band's rule
    const first = items[0];
    const dv =
      markInk === null || first === null || first === undefined
        ? null
        : ink(png, { x: markInk.x1 + 3, y: f.bar.y, w: Math.max(1, first.x0 - 2 - (markInk.x1 + 3)), h: Math.max(1, f.bar.h - 5) }, 0, bg, 20);
    set('divider.w', dv === null ? 0 : dv.w);
    set('divider.h', dv === null ? 0 : dv.h);
    set('divider.left', dv === null ? null : dv.x0 - f.L);
    // the rule: the first row near the bar's bottom where at least 95 % of the stage's width differs from the bar's background
    const bgAt = (x: number, y: number) => {
      const i = (y * png.width + x) * 4;
      return [png.data[i]!, png.data[i + 1]!, png.data[i + 2]!];
    };
    let ruleY = -1;
    for (let y = Math.max(0, Math.floor(f.bar.y + f.bar.h - 8)); y < Math.min(png.height, Math.floor(f.bar.y + f.bar.h + 4)) && ruleY < 0; y++) {
      let hit = 0;
      let total = 0;
      for (let x = Math.round(f.L) + 30; x < f.W - 30; x += 2) {
        const c = bgAt(x, y);
        total++;
        if (Math.max(Math.abs(c[0]! - bg[0]!), Math.abs(c[1]! - bg[1]!), Math.abs(c[2]! - bg[2]!)) >= 12) hit++;
      }
      if (hit / total >= 0.95) ruleY = y;
    }
    set('rule.y', ruleY);
    if (view === 'diff') {
      const t = f.tools.map((r) => ink(png, r, 4, bg));
      set('tool.search.w', t[0]?.w);
      set('tool.search.h', t[0]?.h);
      set('tool.copy.w', t[1]?.w);
      set('tool.gear.w', t[2]?.w);
      set('tool.more.h', t[3]?.h);
      const centres = t.map((i) => (i === null || i === undefined ? NaN : (i.x0 + i.x1) / 2));
      const pitch = centres.length === 4 ? (centres[3]! - centres[0]!) / 3 : NaN;
      set('tool.pitch', pitch);
      const last = t[3];
      set('tool.rightInset', last === null || last === undefined ? null : f.W - (last.x1 + 1));
    }
  } else {
    set('mark.count', f.markCount);
    set('crumb.count', f.crumbCount);
    set('backText.left', backInk === null ? null : backInk.x0 - f.L);
    set('backText.top', backInk?.y0);
    set('backText.w', backInk?.w);
    set('backText.h', backInk?.h);
    set('heading.top', f.heading === null ? null : ink(png, f.heading, 3, bg)?.y0);
    set('subtitle.top', f.subtitle === null ? null : ink(png, f.subtitle, 3, bg)?.y0);
  }
  return out;
}

test(
  "THE INSTRUMENT'S OWN CONTROLS (including the REQUIRED-TAB controls) read as expected before any geometry below is trusted",
  async () => {
    if (skipIfUnavailable()) return;
    const results = await runHeaderControls(browser!);
    const failed = results.filter((r) => !r.ok);
    assert.equal(failed.length, 0, `controls that read wrong: ${failed.map((f) => `${f.control} ${f.why}`).join(' | ')}`);
    assert.ok(results.length >= 15, `the controls ran (${results.length})`);
  },
  STEPS_MS,
);

for (const view of ['diff', 'mission', 'comments'] as const) {
  const spec = fixture.views[view];
  const where = `${spec.reference.file} at ${spec.reference.size[0]} x ${spec.reference.size[1]}`;
  for (const surface of ['specimen', 'real'] as const) {
    test(
      `${view.toUpperCase()} on the ${surface.toUpperCase()}: every header number measures${view === 'mission' ? ' (the Back box as its layout rectangle against hand-measured bounds, plus a painted-ring check; the rest ink against ink)' : ', ink against ink'}, what ITS OWN reference (${where}) requires (targets in stage-header-geometry-targets.FIXTURE.json)`,
      async () => {
        if (skipIfUnavailable()) return;
        const page = await openSurface(surface, view);
        try {
          const got = await measure(page, view);
          // EVIDENCE HOOK (test-only, off by default): when GEO_SHOTS_DIR is set the page that was just measured is also written there as a NEW viewport screenshot
          if (process.env.GEO_SHOTS_DIR) await page.screenshot({ path: path.join(process.env.GEO_SHOTS_DIR, `${view}-${surface}-${spec.reference.size[0]}x${spec.reference.size[1]}.png`) });
          const rows: string[] = [];
          const bad: string[] = [];
          for (const t of spec.targets) {
            const v = got[t.id];
            const ok = v !== undefined && Number.isFinite(v) && Math.abs(v - t.target) <= t.tol;
            rows.push(`${ok ? 'ok ' : 'BAD'} ${t.id.padEnd(20)} lock ${String(t.lock).padStart(5)}  target ${String(t.target).padStart(5)} +-${t.tol}  measured ${v === undefined || !Number.isFinite(v) ? 'absent' : v.toFixed(1)}`);
            if (!ok) bad.push(`${t.id}: measured ${v === undefined || !Number.isFinite(v) ? 'absent' : v.toFixed(1)}, target ${t.target} +-${t.tol} (the reference draws ${t.lock})`);
          }
          console.info(`GEOMETRY ${view} ${surface} (${where}; stage left edge ${spec.reference.stageLeftEdge} in the reference)\n${rows.join('\n')}`);
          // the REQUIRED sibling tabs are collected independently of their box and must all stay reachable wherever the view has them
          if (view !== 'mission') {
            const m = await measureHeader(page);
            assert.deepEqual(tabsNotReachable(m), [], `${view} ${surface}: the required sibling tabs are all present, rendered and larger than 1.5 px`);
            // and the WHOLE of each is pressable: nine points per tab (left, middle and right, 4 px in; top, middle and bottom, 3 px in) must each hit the tab or something inside it:
            // an element-from-point HIT TEST, not a real click. A centre point alone is not enough: the Comments tabs hang 10 px below the Back line into the page's own area, and a page
            // that paints over their LOWER part leaves their centre pressable (found at the geometry restoration by diScanPassB's real click, then by this mutant surviving a centre test)
            const covered = await page.evaluate(() =>
              ['diff', 'herdr', 'comments'].filter((id) => {
                const el = document.querySelector(`[data-testid="di-scan-sibling-${id}"]`);
                if (el === null) return true;
                const r = el.getBoundingClientRect();
                for (const x of [r.x + 4, r.x + r.width / 2, r.x + r.width - 4])
                  for (const y of [r.y + 3, r.y + r.height / 2, r.y + r.height - 3]) {
                    const top = document.elementFromPoint(x, y);
                    if (!(top !== null && (top === el || el.contains(top)))) return true;
                  }
                return false;
              }),
            );
            assert.deepEqual(covered, [], `${view} ${surface}: every point of every required tab is the topmost thing there (nine-point hit test, not a click)`);
          }
          assert.deepEqual(bad, [], `${view} ${surface}: ${bad.length} of ${spec.targets.length} numbers are off the reference`);
          // MISSION ONLY: the layout rectangle is satisfied by a ring of zero contrast, so the ring must ALSO be PAINTED at that boundary (test/diRingPaint.ts). The rule was derived from the REFERENCE first and recorded in the fixture;
          // the negative control (the ring pixels overwritten by the adjacent background, box and chevron unchanged) runs FIRST and must fail every side, or the assertion is worthless
          if (view === 'mission') {
            const rule = fixture.views.mission.paint;
            assert.ok(rule !== undefined && typeof rule.minPeak === 'number', 'the painted-ring rule, derived from the reference first, is recorded in the fixture (views.mission.paint)');
            const { peaks, removed } = await paintOf(page);
            console.info(
              `PAINT ${surface}: peaks ${SIDES.map((s) => `${s} ${peaks[s].peak}@${peaks[s].at}`).join(', ')}; ring removed ${SIDES.map((s) => `${s} ${removed[s].peak}`).join(', ')}; rule: every side >= ${rule.minPeak}`,
            );
            assert.equal(sidesBelow(removed, rule.minPeak).length, 4, `${surface}: NEGATIVE CONTROL, the ring removed (box and chevron unchanged) must fail the paint rule on every side; if it passes the assertion is worthless`);
            assert.deepEqual(sidesBelow(peaks, rule.minPeak), [], `${surface}: every side of the Back box has its perimeter painted at the boundary (peak >= ${rule.minPeak} levels from the adjacent background)`);
          }
        } finally {
          await page.close();
        }
      },
      STEPS_MS,
    );
  }
}

/*
 * DERIVED, NOT A REFERENCE. Below 1500 px there is NO reference for Code Diff or Mission details (their locks are at 1672): the stylesheet sizes them at about 0.72 of the
 * locked values (`--stage-hdr-k`). This asserts only that the restored numbers FOLLOW THAT CONVENTION at 1280 x 720, so that nobody forgets to scale an item: each is within
 * 2.5 px of 0.72 times its locked-scale target. It must never be read as a measurement against a design, and a derived number must never be "corrected" to look like one.
 */
for (const view of ['diff', 'mission'] as const) {
  for (const surface of ['specimen', 'real'] as const) {
    test(
      `DERIVED (not a reference) ${view.toUpperCase()} on the ${surface.toUpperCase()} at 1280 x 720: the scaled header numbers follow the stylesheet's 0.72 convention`,
      async () => {
        if (skipIfUnavailable()) return;
        const page = await openSurface(surface, view, [1280, 720]);
        try {
          const got = await measure(page, view);
          const bad: string[] = [];
          const rows: string[] = [];
          for (const t of fixture.views[view].targets.filter((x) => x.scales === true)) {
            const want = t.target * 0.72;
            const v = got[t.id];
            const ok = v !== undefined && Number.isFinite(v) && Math.abs(v - want) <= 2.5;
            rows.push(`${ok ? 'ok ' : 'BAD'} ${t.id.padEnd(20)} locked-scale ${String(t.target).padStart(5)}  x0.72 = ${want.toFixed(1).padStart(5)}  measured ${v === undefined || !Number.isFinite(v) ? 'absent' : v.toFixed(1)}`);
            if (!ok) bad.push(`${t.id}: measured ${v === undefined || !Number.isFinite(v) ? 'absent' : v.toFixed(1)}, derived ${want.toFixed(1)} +-2.5`);
          }
          console.info(`DERIVED ${view} ${surface} at 1280 x 720 (the stylesheet's own 0.72 convention; NOT a reference)\n${rows.join('\n')}`);
          assert.deepEqual(bad, [], `${view} ${surface}: ${bad.length} derived number(s) do not follow the 0.72 convention`);
        } finally {
          await page.close();
        }
      },
      STEPS_MS,
    );
  }
}

/*
 * CONTROL FOR THE LAYOUT-BOX PATH: the Mission Back numbers read the button's DOM rectangle, so they must follow a box that is MOVED or RESIZED. On the Mission specimen the Back button is made 10 px wider and 9 px
 * further right; its width and stage-relative left must then read OFF their targets (beyond the tolerance), and the unmodified box must read ON them. (None of the instrument's own controls reaches this function.)
 */
test(
  'CONTROL (Mission Back, layout-box path): a Back box that is resized and moved reads as off its targets',
  async () => {
    if (skipIfUnavailable()) return;
    const page = await openSurface('specimen', 'mission');
    try {
      const before = await measure(page, 'mission');
      await page.evaluate(() => {
        const el = document.querySelector('.di-scan-back') as HTMLElement;
        el.style.width = `${el.getBoundingClientRect().width + 10}px`;
        el.style.marginLeft = '9px';
      });
      const after = await measure(page, 'mission');
      const tw = fixture.views.mission.targets.find((t) => t.id === 'back.w')!;
      const tl = fixture.views.mission.targets.find((t) => t.id === 'back.left')!;
      assert.ok(Math.abs(before['back.w']! - tw.target) <= tw.tol && Math.abs(before['back.left']! - tl.target) <= tl.tol, `the unmodified Back box is on its targets (width ${before['back.w']}, left ${before['back.left']})`);
      assert.ok(Math.abs(after['back.w']! - tw.target) > tw.tol, `a Back box 10 px too wide reads off its width target (measured ${after['back.w']})`);
      assert.ok(Math.abs(after['back.left']! - tl.target) > tl.tol, `a Back box 9 px too far right reads off its left target (measured ${after['back.left']})`);
    } finally {
      await page.close();
    }
  },
  STEPS_MS,
);

/*
 * HOME IS UNCHANGED. Home (the scan face's list, governed by v3 01 and the Plan) SHARES the mark and tool classes with the stage header, and this restoration is scoped by view under the
 * stage so that it does not move. Nothing in the suite pinned Home's header before: a mutant that leaked the Code Diff mark rule to every view survived diHomeTokens. These numbers are
 * a BASELINE MEASURED ON THE RESTORED CANDIDATE (a measurement of the product before this restoration was NOT taken: it would have needed file copies, which were stopped); the mark's 36 x 23 equals the value recorded for Home earlier
 * (reports/finding-stage-header-app-vs-specimen-geometry.md section 3). They are NOT a reference and, taken alone, they do not prove that Home did not move: the before/after claim rests on the accepted Home captures staying
 * byte-identical to the accepted manifest, and this test is a regression guard from now on (it turns red when a stage rule leaks to Home). The record (`homeGuard.entries` in the fixture) is per surface and size; a missing entry is printed, never asserted.
 */
interface HomeEntry {
  surface: Surface;
  size: [number, number];
  id: string;
  value: number;
}
async function measureHome(page: Page): Promise<Record<string, number>> {
  return page.evaluate(() => {
    const r = (el: Element | null) => (el === null ? null : el.getBoundingClientRect());
    const head = document.querySelector('.di-scan-header');
    const out: Record<string, number> = {};
    const svg = r(head?.querySelector('.di-scan-mark svg') ?? null);
    const btn = r(head?.querySelector('.di-scan-mark') ?? null);
    const tools = [...(head?.querySelectorAll('.di-scan-header-tools > button') ?? [])].map((b) => b.getBoundingClientRect());
    out['home.markSvg.w'] = svg === null ? NaN : +svg.width.toFixed(1);
    out['home.markSvg.h'] = svg === null ? NaN : +svg.height.toFixed(1);
    out['home.markBtn.w'] = btn === null ? NaN : +btn.width.toFixed(1);
    out['home.markBtn.h'] = btn === null ? NaN : +btn.height.toFixed(1);
    out['home.tools.n'] = tools.length;
    out['home.tool0.w'] = tools[0] === undefined ? NaN : +tools[0].width.toFixed(1);
    out['home.tool0.h'] = tools[0] === undefined ? NaN : +tools[0].height.toFixed(1);
    out['home.tools.gap'] = tools.length > 1 ? +(tools[1]!.x - (tools[0]!.x + tools[0]!.width)).toFixed(1) : NaN;
    out['home.header.h'] = head === null ? NaN : +head.getBoundingClientRect().height.toFixed(1);
    return out;
  });
}
async function openHome(surface: Surface, size: [number, number]): Promise<Page> {
  const page = await browser!.newPage({ viewport: { width: size[0], height: size[1] } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  if (surface === 'specimen') {
    await page.goto(`http://localhost:${port}/test/harness/di-scan-lock.html`, { waitUntil: 'domcontentloaded' });
  } else {
    const ready = new Promise<WebSocket>((resolve) => (onReady = resolve));
    await page.goto(`http://localhost:${port}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
    await ready;
    socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
    socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
    socket!.send(JSON.stringify(doc.states.live));
    await page.waitForSelector('[data-testid="di-rail-orchestrator"]');
  }
  await page.waitForSelector('.di-scan-header .di-scan-mark');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return page;
}
const homeGuard = (fixture as unknown as { homeGuard?: { entries: HomeEntry[] } }).homeGuard ?? { entries: [] };
for (const surface of ['specimen', 'real'] as const) {
  for (const size of [[1280, 720], [1672, 941]] as Array<[number, number]>) {
    test(
      `HOME is UNCHANGED on the ${surface.toUpperCase()} at ${size[0]} x ${size[1]}: the header's mark and tool buttons keep the sizes recorded as Home's baseline (measured on the restored candidate; a baseline, not a reference)`,
      async () => {
        if (skipIfUnavailable()) return;
        const page = await openHome(surface, size);
        try {
          const got = await measureHome(page);
          console.info(`HOME ${surface} ${size[0]}x${size[1]}: ${JSON.stringify(got)}`);
          const mine = homeGuard.entries.filter((e) => e.surface === surface && e.size[0] === size[0]);
          const bad = mine.filter((e) => !(Math.abs((got[e.id] ?? NaN) - e.value) <= 0.6)).map((e) => `${e.id}: measured ${got[e.id]}, baseline ${e.value}`);
          assert.deepEqual(bad, [], `${surface} ${size[0]}: Home moved`);
          assert.ok(mine.length > 0 || process.env.HOME_RECORD === '1', `${surface} ${size[0]}: no Home baseline recorded (run once with HOME_RECORD=1 to print it, then record it)`);
        } finally {
          await page.close();
        }
      },
      STEPS_MS,
    );
  }
}
