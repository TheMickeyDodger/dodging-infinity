/**
 * B3 COMPOSED EDITOR BAR (the Lead, two composition checks). Green-4 measured the default 1440 editor bar's path at 39px BEFORE Wrap and the Copy outcome existed; the bar is one flex row with the path at min-width 0, so added
 * controls can squeeze it toward nothing. 1C's G3 proved focus SETTLES on di-scan-editor-path BY IDENTITY; it did NOT prove the label stays SEEN. THIS INPUT measures the COMPOSED bar at the two shipping sizes:
 *   BAR     a real file with the Wrap control on and the Copy outcome visible (the widest bar): the path label is SEEN by the calibrated helper, every control in the row lies inside the bar and none overlaps another, and the bar does not
 *           overflow its own width. The longest READABLE path a 103-file collection ISSUES (measured from the issued set, not assumed from where it was appended), with the Copy outcome showing, is the same measurement.
 *   LANDING the destination 1C's Refresh handoff uses, SEEN where focus really lands: after a file's refused read is refreshed, and after the OVERVIEW's refused read is refreshed with no file chosen (the label is then EMPTY: the cell records its text).
 * The fix for a squeezed label is the bar's layout, not a quieter landing. Records with the prefix B3ST. Chrome, not Brave; the clipboard is a STUB.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import type { WebSocket as ServerSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3ST_SHOTS ?? null;


let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** B3 1C G3: refuse the file reads the way a moved workspace does, and count the reads asked for. */
let refuseFiles = false;
let overviewReads = 0;
let fileReads = 0;
let refuseOverview = false;
let holdOverview = false;
let heldOverview: (() => void) | null = null;
/** B3 routes: the number of files the overview lists (null: the fixture's own 23). Fewer: the first N; more: synthetic text files appended. The totals are recomputed so the listing stays consistent. */
let collectionSize: number | null = null;
/** The files the overview lists for a collection of N: the fixture's own first N, or its 23 plus synthetic text files. ONE function, so the page and a test that picks a file from the issued set agree. */
type OverviewFiles = NonNullable<DiCodeReview['overview']>['files'];
function collectionFiles(base: OverviewFiles, n: number): OverviewFiles {
  return n <= base.length ? base.slice(0, n) : [...base, ...Array.from({ length: n - base.length }, (_, i) => ({ ...base[base.length - 1]!, fileId: `syn-${i}`, path: `synthetic/very/deeply/nested/directory/set-${i % 9}/a-rather-long-file-name-${i}.txt`, oldPath: null, change: 'modified', state: 'text', reason: null, additions: 1, deletions: 0 }))];
}

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  if (stored === undefined && typeof request.fileId === 'string' && request.fileId.startsWith('syn-')) {
    // a synthetic file is READABLE: the sample file's real lines under its own identity and its long path
    const model = Object.values(doc.replies.files).find((r) => r.file?.path === 'src/syntax_sample.ts');
    const entry = collectionFiles(doc.replies.overview.overview!.files, collectionSize ?? 0).find((f) => f.fileId === request.fileId);
    if (model === undefined || entry === undefined || model.file === null) return null;
    const synthetic = structuredClone(model);
    synthetic.fileId = entry.fileId;
    synthetic.file!.fileId = entry.fileId;
    synthetic.file!.path = entry.path;
    return synthetic;
  }
  if (stored === undefined) return null;
  const reply = structuredClone(stored);
  if (request.fileId === undefined && collectionSize !== null && reply.overview !== null) {
    const files = collectionFiles(reply.overview.files, collectionSize);
    reply.overview = { files, totals: { files: files.length, additions: files.reduce((n, f) => n + (f.additions ?? 0), 0), deletions: files.reduce((n, f) => n + (f.deletions ?? 0), 0), withheld: files.filter((f) => f.state === 'withheld').length, unshown: files.filter((f) => f.state !== 'text' && f.state !== 'submodule').length } };
  }
  return reply;
}

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false, ws: false }, logLevel: 'silent' });
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
          resolveConnected(ws);
        } else if (msg.type === 'diCodeReviewRead' || msg.type === 'diCodeReviewMissionRead') {
          const found = replyFor(msg);
          if (found === null) return;
          if (msg.fileId === undefined) overviewReads += 1;
          else fileReads += 1;
          const reply =
            refuseFiles && msg.fileId !== undefined
              ? { ...found, snapshot: String(msg.snapshot), state: 'refused', decidedBy: 'backend', problem: 'snapshot_moved', detail: 'moved', target: null, comparison: null, file: null, overview: null }
              : found;
          const answer =
            msg.fileId === undefined && refuseOverview
              ? { ...reply, state: 'refused', decidedBy: 'backend', problem: 'synthetic_unreadable', detail: 'SYNTHETIC: the review could not be read', target: null, comparison: null, file: null, overview: null }
              : reply;
          const payload = JSON.stringify(typeof msg.requestId === 'string' ? { ...answer, requestId: msg.requestId } : answer);
          if (msg.fileId === undefined && holdOverview) {
            heldOverview = () => ws.send(payload);
            return;
          }
          ws.send(payload);
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
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
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B3 states): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  refuseFiles = false;
  overviewReads = 0;
  fileReads = 0;
  refuseOverview = false;
  holdOverview = false;
  heldOverview = null;
  collectionSize = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B3ST ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B3ST SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(doc.states.live));
  await p().waitForSelector('[data-testid="di-rail-orchestrator"]');
}

async function openCodeDiff(): Promise<void> {
  await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
  await p().waitForSelector('[data-testid="di-scan-tree-file"]');
}

async function selectFile(name: string): Promise<void> {
  await p().locator('[data-testid="di-scan-tree-file"]', { hasText: name }).first().click();
  await p().waitForFunction((n) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(n), name, { timeout: 15_000 });
  await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
  await p().waitForTimeout(200);
}


const SIZES = [
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const sel = (id: string) => `[data-testid="${id}"]`;
const focusKey = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
const refreshButtons = () => p().getByRole('button', { name: 'Refresh', exact: true });
// ── SEEN: the calibrated visibility helper (shared by the B3 inputs). VERSION 2 (version 1 reported false negatives, found by the first composed run: it treated every overflow ancestor as clipping, but an overflow ancestor clips only the descendants whose
// CONTAINING BLOCK chain it is in, so a fixed or absolutely positioned control outside it is NOT clipped; and it sampled the rounded CORNERS of a box, where the element is not hit). "Seen" means a person could see it: it has a drawn box, is not hidden by CSS,
// lies inside every ancestor that actually CLIPS it (along its containing-block chain) and the window, and is the topmost element at its centre and the middles of its four edges (not covered). It returns the reasons it is not seen. It is CALIBRATED before it is trusted (calibrateSeen): it must say yes to a plain
// control and no to one clipped by an overflow ancestor, one covered by an overlay and one hidden by a 1px clip; a helper that cannot say no proves nothing.
interface SeenInfo {
  seen: boolean;
  why: string[];
  width: number;
  height: number;
}
const seenIn = (selector: string): SeenInfo => {
  const el = document.querySelector<HTMLElement>(selector);
  if (el === null) return { seen: false, why: ['absent'], width: 0, height: 0 };
  const why: string[] = [];
  const cs = getComputedStyle(el);
  if (cs.display === 'none') why.push('display:none');
  if (cs.visibility !== 'visible') why.push(`visibility:${cs.visibility}`);
  if (Number(cs.opacity) === 0) why.push('opacity:0');
  if (cs.clipPath !== 'none') why.push(`clip-path:${cs.clipPath}`);
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) why.push(`no box (${r.width.toFixed(1)}x${r.height.toFixed(1)})`);
  if (r.right <= 0 || r.bottom <= 0 || r.left >= window.innerWidth || r.top >= window.innerHeight) why.push('outside the window');
  const escapes = (a: Element) => {
    const c = getComputedStyle(a);
    return c.transform !== 'none' || c.filter !== 'none' || c.perspective !== 'none' || /paint|layout|strict|content/.test(c.contain) || c.willChange.includes('transform');
  };
  /** The box that bounds this element's painting: the viewport for a fixed element (unless a transformed ancestor holds it), the nearest positioned ancestor for an absolute one, else the parent. */
  const containingBlock = (node: Element): Element | null => {
    const pos = getComputedStyle(node).position;
    let a = node.parentElement;
    if (pos === 'fixed') {
      while (a !== null && !escapes(a)) a = a.parentElement;
      return a;
    }
    if (pos === 'absolute') {
      while (a !== null && getComputedStyle(a).position === 'static' && !escapes(a)) a = a.parentElement;
      return a;
    }
    return a;
  };
  for (let a = containingBlock(el); a !== null; a = containingBlock(a)) {
    const s = getComputedStyle(a);
    if (s.overflowX === 'visible' && s.overflowY === 'visible' && s.clipPath === 'none') continue;
    const ar = a.getBoundingClientRect();
    const ix = Math.min(r.right, ar.right) - Math.max(r.left, ar.left);
    const iy = Math.min(r.bottom, ar.bottom) - Math.max(r.top, ar.top);
    if (ix < r.width - 0.5 || iy < r.height - 0.5) why.push(`clipped by ${a.getAttribute('data-testid') ?? (typeof a.className === 'string' ? a.className.split(' ')[0] : a.tagName)}`);
  }
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const points: Array<[number, number]> = [
    [cx, cy],
    [r.left + 2, cy],
    [r.right - 2, cy],
    [cx, r.top + 2],
    [cx, r.bottom - 2],
  ];
  for (const [x, y] of points) {
    if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (top === null || !(top === el || el.contains(top))) {
      why.push(`covered at ${Math.round(x)},${Math.round(y)} by ${top?.getAttribute('data-testid') ?? top?.tagName ?? 'nothing'}`);
      break;
    }
  }
  return { seen: why.length === 0, why, width: r.width, height: r.height };
};
const seen = (selector: string): Promise<SeenInfo> => p().evaluate(seenIn, selector);
/** Inject four known controls, check the helper's verdict on each, then remove them. */
async function calibrateSeen(): Promise<void> {
  await p().evaluate(() => {
    const host = document.createElement('div');
    host.id = 'seen-cal';
    host.style.cssText = 'position:fixed;left:8px;top:8px;z-index:2147483000';
    host.innerHTML =
      '<button id="cal-visible" style="display:block;width:120px;height:28px">visible</button>' +
      '<div style="width:20px;height:20px;overflow:hidden;position:relative"><button id="cal-clipped" style="position:absolute;left:60px;top:0;width:80px;height:20px">clipped</button></div>' +
      '<button id="cal-covered" style="display:block;width:120px;height:28px">covered</button>' +
      '<button id="cal-hidden" style="position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)">hidden</button>' +
      '<button id="cal-round" style="display:block;width:120px;height:28px;border-radius:14px">rounded</button>' +
      '<div style="width:20px;height:20px;overflow:hidden;position:relative"><button id="cal-fixed" style="position:fixed;left:200px;top:8px;width:80px;height:20px">fixed escapee</button></div>';
    document.body.appendChild(host);
    const covered = document.getElementById('cal-covered')!.getBoundingClientRect();
    const cover = document.createElement('div');
    cover.id = 'cal-cover';
    cover.style.cssText = `position:fixed;left:${covered.left - 2}px;top:${covered.top - 2}px;width:${covered.width + 4}px;height:${covered.height + 4}px;z-index:2147483647`;
    document.body.appendChild(cover);
  });
  const verdicts = { visible: (await seen('#cal-visible')).seen, clipped: (await seen('#cal-clipped')).seen, covered: (await seen('#cal-covered')).seen, hidden: (await seen('#cal-hidden')).seen, rounded: (await seen('#cal-round')).seen, fixedEscapee: (await seen('#cal-fixed')).seen };
  await p().evaluate(() => {
    document.getElementById('seen-cal')?.remove();
    document.getElementById('cal-cover')?.remove();
  });
  assert.deepEqual(verdicts, { visible: true, clipped: false, covered: false, hidden: false, rounded: true, fixedEscapee: true }, 'CALIBRATION: the SEEN helper says yes to a plain, a rounded and a fixed-position control that an overflow ancestor does not clip, and no to a clipped, a covered and a 1px-hidden one');
}

interface Fit {
  found: boolean;
  ids: string[];
  boxes: Array<{ id: string; left: number; right: number; top: number; bottom: number; width: number }>;
  overlaps: string[];
  outside: string[];
  overflow: { scrollWidth: number; clientWidth: number };
}
/** What the bar's controls ARE (buttons, selects, inputs, the path label, the Copy outcome, the fixture tag), wherever they sit in it. */
const FIT_LEAVES = 'button, select, input, [data-leaf], [data-testid="di-scan-editor-path"], [data-testid="di-scan-diff-copy-result"], .di-scan-fixture-tag';
/**
 * The bar and its controls, measured STRUCTURALLY. With no selector the bar is the lowest ancestor of the path label that also holds the Files toggle and the Wrap control (so grouping or nesting the controls does
 * not change the scope), and its controls are every visible leaf control inside it at any depth. Reports each box, any two that overlap (by area), any that lie outside the bar, and whether the bar overflows its own width.
 */
const fitReport = (rootSelector: string | null): Promise<Fit> =>
  p().evaluate(
    ({ rootSelector, leaves }): Fit => {
      let root: Element | null;
      if (rootSelector === null) {
        root = document.querySelector('[data-testid="di-scan-editor-path"]');
        while (root !== null && !(root.querySelector('[data-testid="di-scan-files-toggle"]') !== null && root.querySelector('[data-testid="di-scan-wrap-toggle"]') !== null)) root = root.parentElement;
      } else root = document.querySelector(rootSelector);
      if (root === null) return { found: false, ids: [], boxes: [], overlaps: [], outside: [], overflow: { scrollWidth: 0, clientWidth: 0 } };
      const rb = root.getBoundingClientRect();
      const boxes = Array.from(root.querySelectorAll<HTMLElement>(leaves))
        .filter((el) => !el.classList.contains('di-scan-visually-hidden') && getComputedStyle(el).visibility === 'visible')
        .map((el) => ({ el, r: el.getBoundingClientRect() }))
        .filter(({ r }) => r.width >= 2 && r.height >= 2)
        .map(({ el, r }) => ({ id: el.getAttribute('data-testid') ?? el.getAttribute('aria-label') ?? el.getAttribute('data-leaf') ?? `${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}`, left: Math.round(r.left * 10) / 10, right: Math.round(r.right * 10) / 10, top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10, width: Math.round(r.width) }));
      const overlaps: string[] = [];
      for (let i = 0; i < boxes.length; i += 1)
        for (let j = i + 1; j < boxes.length; j += 1) {
          const a = boxes[i]!;
          const b = boxes[j]!;
          const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (w > 0.5 && h > 0.5) overlaps.push(`${a.id} overlaps ${b.id} (${w.toFixed(1)}x${h.toFixed(1)}px)`);
        }
      const outside = boxes.filter((b) => b.left < rb.left - 0.5 || b.right > rb.right + 0.5 || b.top < rb.top - 0.5 || b.bottom > rb.bottom + 0.5).map((b) => `${b.id} ${b.left}..${b.right} not in ${rb.left.toFixed(1)}..${rb.right.toFixed(1)}`);
      return { found: true, ids: boxes.map((b) => b.id), boxes, overlaps, outside, overflow: { scrollWidth: (root as HTMLElement).scrollWidth, clientWidth: (root as HTMLElement).clientWidth } };
    },
    { rootSelector, leaves: FIT_LEAVES },
  );
/** The fit report must say yes to a bar that fits, and name an overlap and an outside control: calibrated before it is trusted. */
async function calibrateFit(): Promise<void> {
  await p().evaluate(() => {
    const host = document.createElement('div');
    host.id = 'fit-host';
    host.style.cssText = 'position:fixed;left:8px;top:8px;z-index:2147483000';
    host.innerHTML =
      '<div id="fit-ok" style="position:relative;width:200px;height:30px"><span data-leaf="a" style="position:absolute;left:0;top:0;width:90px;height:20px">a</span><span data-leaf="b" style="position:absolute;left:100px;top:0;width:90px;height:20px">b</span></div>' +
      '<div id="fit-overlap" style="position:relative;width:200px;height:30px"><span data-leaf="a" style="position:absolute;left:0;top:0;width:90px;height:20px">a</span><span data-leaf="b" style="position:absolute;left:50px;top:0;width:90px;height:20px">b</span></div>' +
      '<div id="fit-outside" style="position:relative;width:100px;height:30px"><span data-leaf="a" style="position:absolute;left:0;top:0;width:90px;height:20px">a</span><span data-leaf="b" style="position:absolute;left:150px;top:0;width:90px;height:20px">b</span></div>';
    document.body.appendChild(host);
  });
  const ok = await fitReport('#fit-ok');
  const overlap = await fitReport('#fit-overlap');
  const outside = await fitReport('#fit-outside');
  await p().evaluate(() => document.getElementById('fit-host')?.remove());
  assert.deepEqual({ ok: [ok.overlaps.length, ok.outside.length], overlap: overlap.overlaps.length > 0, outside: outside.outside.length > 0 }, { ok: [0, 0], overlap: true, outside: true }, 'CALIBRATION: the fit report says yes to a bar that fits, names an overlap, and names a control outside the bar');
}
async function stubClipboardRefused(): Promise<void> {
  await p().evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new DOMException('denied', 'NotAllowedError')) }, configurable: true });
  });
}
/** The controls the composed bar must be seen to hold: a scope that silently dropped one would report a fitting bar that does not fit. */
const BAR_MUST_HOLD = ['di-scan-files-toggle', 'di-scan-editor-path', 'di-scan-wrap-toggle', 'Diff layout'];
async function assertBar(where: string, fit: Fit, pathSeen: SeenInfo, extra: string[] = []): Promise<void> {
  assert.equal(fit.found, true, `STATE: ${where}: the bar is found by structure`);
  for (const id of [...BAR_MUST_HOLD, ...extra]) assert.ok(fit.ids.includes(id), `CONTRACT (SCOPE): ${where}: the enumerated controls include ${id} (found: ${fit.ids.join(', ')})`);
  assert.equal(pathSeen.seen, true, `CONTRACT (SEEN): ${where}: the path label is seen (${pathSeen.why.join('; ') || 'ok'}; ${pathSeen.width.toFixed(0)}x${pathSeen.height.toFixed(0)}px)`);
  assert.deepEqual(fit.outside, [], `CONTRACT (FIT): ${where}: every control in the bar lies inside it`);
  assert.deepEqual(fit.overlaps, [], `CONTRACT (FIT): ${where}: no control in the bar overlaps another`);
  assert.ok(fit.overflow.scrollWidth <= fit.overflow.clientWidth + 1, `CONTRACT (FIT): ${where}: the bar does not overflow its own width (${fit.overflow.scrollWidth} > ${fit.overflow.clientWidth})`);
}
/** Every focus move and path-label (re)mount with its time, so a landing that does not settle can be READ from a trace in the failure message instead of inferred (compose-4: the 1600 overview landing read BODY once and 3 of 3 repeats in isolation passed). */
async function installTrace(): Promise<void> {
  await p().evaluate(() => {
    const w = window as unknown as { __trace: string[] };
    w.__trace = [];
    const t0 = performance.now();
    const name = (el: EventTarget | null) => (el instanceof Element ? (el.getAttribute('data-testid') ?? `${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}`) : String(el));
    document.addEventListener('focusin', (e) => w.__trace.push(`+${Math.round(performance.now() - t0)}ms focusin ${name(e.target)}`), true);
    document.addEventListener('focusout', (e) => w.__trace.push(`+${Math.round(performance.now() - t0)}ms focusout ${name(e.target)} -> ${name((e as FocusEvent).relatedTarget)}`), true);
    new MutationObserver(() => {
      const path = document.querySelector('[data-testid="di-scan-editor-path"]');
      const state = path === null ? 'path ABSENT' : 'path PRESENT';
      if (!(w.__trace[w.__trace.length - 1] ?? '').endsWith(state)) w.__trace.push(`+${Math.round(performance.now() - t0)}ms ${state}`);
    }).observe(document.body, { childList: true, subtree: true });
  });
}
const traceOf = () => p().evaluate(() => (window as unknown as { __trace?: string[] }).__trace ?? []);
/** Wait (bounded) for the landing to happen, then let it prove it STAYS. */
async function settleOnPath(): Promise<void> {
  await p().waitForFunction(() => document.activeElement?.getAttribute('data-testid') === 'di-scan-editor-path', undefined, { timeout: 6_000 }).catch(() => undefined);
  await p().waitForTimeout(600);
}
const pathText = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '');

for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 COMPOSED BAR, at ${where}, BAR: with Wrap on and a refused Copy outcome showing, the path label is SEEN and every control in the row fits`, async () => {
    await boot(vp);
    await openCodeDiff();
    await calibrateSeen();
    await calibrateFit();
    await selectFile('syntax_sample.ts');
    assert.equal(await p().locator(sel('di-scan-wrap-toggle')).count(), 1, 'STATE: the composed bar offers the Wrap control');
    await p().locator(sel('di-scan-wrap-toggle')).click();
    await stubClipboardRefused();
    await p().locator(sel('di-scan-diff-copy')).focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-scan-diff-copy-result'), { timeout: 5_000 });
    const fit = await fitReport(null);
    const pathSeen = await seen(sel('di-scan-editor-path'));
    record(`BAR ${vp.width}x${vp.height}`, { boxes: fit.boxes, pathSeen, pathText: await pathText() });
    await shot(`b3bar-${vp.width}x${vp.height}.png`);
    await assertBar(where, fit, pathSeen, ['di-scan-diff-copy-result']);
  });
  test(`B3 COMPOSED BAR, at ${where}, BAR (longest readable issued path): the longest readable path the 103-file collection issues, with Wrap on and a Copy outcome showing, is the same measurement`, async () => {
    collectionSize = 103;
    // the ISSUED set, from the same function the page is served from: the longest READABLE path is MEASURED, never assumed from where it was appended
    const issued = collectionFiles(doc.replies.overview.overview!.files, 103);
    const readable = (f: (typeof issued)[number]) => f.state === 'text' || f.state === 'submodule';
    const longest = issued.filter(readable).reduce((a, f) => (f.path.length > a.path.length ? f : a));
    const real = Math.max(...doc.replies.overview.overview!.files.filter(readable).map((f) => f.path.length));
    await boot(vp);
    await openCodeDiff();
    await calibrateSeen();
    await calibrateFit();
    assert.equal(issued.length, 103, 'STATE: the collection issues 103 files');
    assert.ok(longest.path.length > real, `STATE: the longest readable issued path (${longest.path.length} chars) is longer than the fixture's own longest readable path (${real})`);
    const row = p().locator(`${sel('di-scan-tree-file')}[data-file-id="${longest.fileId}"]`);
    assert.equal(await row.count(), 1, `STATE: the longest readable issued file (${longest.fileId}) exists in the tree`);
    await row.click();
    await p().waitForFunction((want) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(want), longest.path, { timeout: 15_000 });
    await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
    await p().locator(sel('di-scan-wrap-toggle')).click();
    await stubClipboardRefused();
    await p().locator(sel('di-scan-diff-copy')).focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-scan-diff-copy-result'), { timeout: 5_000 });
    const fit = await fitReport(null);
    const pathSeen = await seen(sel('di-scan-editor-path'));
    record(`BAR-LONG ${vp.width}x${vp.height}`, { longestReadableIssued: { id: longest.fileId, path: longest.path, chars: longest.path.length }, fixtureLongestReadableChars: real, boxes: fit.boxes, pathSeen });
    await shot(`b3bar-long-${vp.width}x${vp.height}.png`);
    await assertBar(`${where}, longest readable issued path`, fit, pathSeen, ['di-scan-diff-copy-result']);
  });
  test(`B3 COMPOSED BAR, at ${where}, LANDING (a file's refused read): after Refresh the path label is where focus lands AND it is SEEN, naming the file`, async () => {
    await boot(vp);
    await openCodeDiff();
    await calibrateSeen();
    await calibrateFit();
    refuseFiles = true;
    await p().locator(sel('di-scan-tree-file'), { hasText: 'syntax_sample.ts' }).first().click();
    await p().waitForSelector(`${sel('di-scan-file-notice')}[data-state="refused"]`, { timeout: 15_000 });
    refuseFiles = false;
    await refreshButtons().focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
    await settleOnPath();
    const focus = await focusKey();
    const fit = await fitReport(null);
    const pathSeen = await seen(sel('di-scan-editor-path'));
    const text = await pathText();
    record(`LANDING-FILE ${vp.width}x${vp.height}`, { focus, text, pathSeen, reads: { overview: overviewReads, file: fileReads } });
    assert.equal(focus, 'di-scan-editor-path', 'STATE: focus settled on the path label');
    assert.ok(text.includes('syntax_sample.ts'), `CONTRACT (MEANINGFUL): the landing names the file (${JSON.stringify(text)})`);
    await assertBar(`${where}, landing`, fit, pathSeen);
  });
  test(`B3 COMPOSED BAR, at ${where}, LANDING (the overview's refused read, no file chosen): where focus lands after the late read, and whether it is SEEN and meaningful`, async () => {
    refuseOverview = true;
    await boot(vp);
    await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await p().click('[data-testid="di-scan-code-diff"]');
    await p().waitForSelector(sel('di-scan-diff-refused'), { timeout: 15_000 });
    await calibrateSeen();
    await installTrace();
    refuseOverview = false;
    holdOverview = true;
    await refreshButtons().focus();
    await p().keyboard.press('Enter');
    for (let i = 0; i < 40 && heldOverview === null; i++) await p().waitForTimeout(100);
    assert.ok(heldOverview !== null, 'STATE: the next read is held until released');
    holdOverview = false;
    heldOverview?.();
    await p().waitForSelector(sel('di-scan-tree-file'), { timeout: 15_000 });
    await settleOnPath();
    const focus = await focusKey();
    const pathSeen = await seen(sel('di-scan-editor-path'));
    const text = await pathText();
    const trace = await traceOf();
    record(`LANDING-OVERVIEW ${vp.width}x${vp.height}`, { focus, text, pathSeen, reads: { overview: overviewReads, file: fileReads } });
    assert.equal(focus, 'di-scan-editor-path', `STATE: focus settled on the path label (trace: ${trace.join(' | ')})`);
    assert.equal(pathSeen.seen, true, `CONTRACT (SEEN): the landing is seen (${pathSeen.why.join('; ') || 'ok'}; ${pathSeen.width.toFixed(0)}x${pathSeen.height.toFixed(0)}px)`);
    assert.ok(text.trim().length > 0, `CONTRACT (MEANINGFUL): the landing says something (the label reads ${JSON.stringify(text)} while no file is chosen)`);
  });
}

// ── CONTROLLED reproduction (compose-4's overview landing read BODY once at 1600; CPU throttle x6 in the page's own browser reproduces it, 2 of 10 unlogged and 5 of 10 with the effect logged, and the log named the cause: the first effect after the click ran on an interim render whose placeholder was still the OLD refusal and the "read ended again" rule dropped the landing intent) ──
/** TypeScript narrows a local `heldOverview = null` to never, and a later `heldOverview?.()` is then "not callable"; resetting through a function keeps the declared type (compose-7: tsc -b caught it). */
function resetOverviewHold(): void {
  holdOverview = false;
  heldOverview = null;
}
test('B3 COMPOSED BAR, at 1600x1000, LANDING UNDER LOAD (CPU x6, eight executions): after the overview\'s late read focus lands on the path label EVERY time, by identity', async () => {
  const vp = { width: 1600, height: 1000 };
  const outcomes: Array<{ n: number; focus: string; landedAfterMs: number | null; text: string }> = [];
  for (let n = 1; n <= 8; n += 1) {
    refuseOverview = true;
    resetOverviewHold();
    await boot(vp);
    await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await p().click('[data-testid="di-scan-code-diff"]');
    await p().waitForSelector(sel('di-scan-diff-refused'), { timeout: 15_000 });
    await installTrace();
    const cdp = await p().context().newCDPSession(p());
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
    refuseOverview = false;
    holdOverview = true;
    await refreshButtons().focus();
    await p().keyboard.press('Enter');
    for (let i = 0; i < 80 && heldOverview === null; i += 1) await p().waitForTimeout(100);
    holdOverview = false;
    heldOverview?.();
    const t0 = Date.now();
    let landedAfterMs: number | null = null;
    for (let i = 0; i < 200 && landedAfterMs === null; i += 1) {
      if ((await focusKey()) === 'di-scan-editor-path') landedAfterMs = Date.now() - t0;
      else await p().waitForTimeout(50);
    }
    await p().waitForTimeout(600);
    outcomes.push({ n, focus: await focusKey(), landedAfterMs, text: await pathText() });
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    await p().close();
  }
  record('LANDING-UNDER-LOAD 1600x1000', { outcomes });
  assert.deepEqual(outcomes.filter((o) => o.focus !== 'di-scan-editor-path' || o.landedAfterMs === null).map((o) => `execution ${o.n}: focus ${o.focus}`), [], 'CONTRACT (LANDS UNDER LOAD): every execution lands on the path label within the bound and stays');
}, STEPS_MS);

// ── SAME-SURFACE KEYS (the Lead's source concern about the first listener-based guard: a modifier or an ArrowDown pressed on the reading placeholder relocates nothing; it must not cancel the only handoff) ──
async function toHeldRefresh(vp: { width: number; height: number }): Promise<void> {
  refuseOverview = true;
  resetOverviewHold();
  await boot(vp);
  await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector(sel('di-scan-diff-refused'), { timeout: 15_000 });
  await installTrace();
  refuseOverview = false;
  holdOverview = true;
  await refreshButtons().focus();
  await p().keyboard.press('Enter');
  for (let i = 0; i < 80 && heldOverview === null; i += 1) await p().waitForTimeout(100);
  assert.ok(heldOverview !== null, 'STATE: the next read is held');
}
test('B3 COMPOSED BAR, at 1600x1000, LANDING SAME-SURFACE KEYS: a modifier and an ArrowDown pressed on the reading placeholder do not cancel the handoff; the late read lands focus on the path label, by identity', async () => {
  await toHeldRefresh({ width: 1600, height: 1000 });
  const onPlaceholder = await focusKey();
  for (const key of ['Shift', 'Control', 'ArrowDown', 'ArrowDown', 'Alt']) await p().keyboard.press(key);
  const afterKeys = await focusKey();
  holdOverview = false;
  heldOverview?.();
  await settleOnPath();
  const focus = await focusKey();
  const trace = await traceOf();
  record('LANDING-SAME-SURFACE-KEYS 1600x1000', { onPlaceholder, afterKeys, focus, trace });
  assert.equal(onPlaceholder, 'di-scan-diff-reading', 'STATE: Refresh left focus on the reading placeholder');
  assert.equal(afterKeys, 'di-scan-diff-reading', 'STATE: the keys relocated nothing (focus is still on the reading placeholder)');
  assert.equal(focus, 'di-scan-editor-path', `CONTRACT (SAME SURFACE): after keys that relocate nothing, the late read lands focus on the path label (trace: ${trace.join(' | ')})`);
}, STEPS_MS);
test('B3 COMPOSED BAR, at 1600x1000, LANDING AFTER A DELIBERATE MOVE BY TAB: where the person put focus is kept when the late read arrives (measured, not assumed)', async () => {
  await toHeldRefresh({ width: 1600, height: 1000 });
  await p().keyboard.press('Tab');
  const moved = await p().evaluate(() => {
    const a = document.activeElement;
    return { id: a?.getAttribute('data-testid') ?? a?.tagName ?? 'none', insideEditor: a instanceof HTMLElement && a.closest('.di-scan-editor') !== null };
  });
  holdOverview = false;
  heldOverview?.();
  await settleOnPath();
  const focus = await focusKey();
  const trace = await traceOf();
  record('LANDING-AFTER-TAB 1600x1000', { moved, focus, trace });
  assert.notEqual(moved.id, 'di-scan-diff-reading', 'STATE: Tab moved focus off the reading placeholder');
  assert.equal(focus, moved.id, `CONTRACT (NO STEAL): focus stays where the person put it with Tab (${moved.id}, inside the editor: ${moved.insideEditor}), trace: ${trace.join(' | ')}`);
}, STEPS_MS);
