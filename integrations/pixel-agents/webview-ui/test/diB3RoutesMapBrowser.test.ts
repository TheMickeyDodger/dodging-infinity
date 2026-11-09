/**
 * B3 ROUTES, from Home to the Map and the minimized surfaces, and back (the Lead's bar: a route whose cost does NOT GROW with the Mission collection). Records with the prefix B3RT.
 * MEASURED FIRST (b3-routes/probe-p1.log): the world controls are LAST in the page and behind the disclosure, so the old Shift+Tab route (K1's 34) is a wrap-around the end; forward, the cost is every Mission row.
 *   FORWARD  from Home's search: the jump "Go to the Map" (it opens the disclosure when closed) lands on the Map's toggle. BACK: from the Map's toggle, "Back to the Mission list" lands on Home's search.
 *   INVARIANCE: the SAME route is measured with 4, 64 and 192 Missions; the presses (Tab to the jump, plus Enter) must be identical. CALIBRATION: the plain route (Tab on past every row, the disclosure open) must GROW over the
 *   same collections, so an invariant jump is a property of the control and not of a measurement that cannot see growth.
 *   SEEN: each jump is hidden until focused and then genuinely visible (the calibrated helper). The unchanged K1 route witness (Escape closes the Map, focus stays on the toggle) is run beside this input.
 * Chrome, not Brave; the Mission collection is the fixture's, trimmed or tripled.
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

import type { DiOfficeState } from '../../core/src/messages.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const metropolis = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
/** B3 routes: the size of the Mission collection the page is given. 'small' = the first 4 Missions; 'large' = the fixture's Missions three times over (copies with their own references); null = the fixture as it is. */
type Collection = 'small' | 'large' | null;
let collection: Collection = null;
/** The references of the first four Missions the page DRAWS as rows (found by looking, not by position in the state: the first four in the state are not rows). */
let smallRefs: string[] | null = null;
function shaped(state: DiOfficeState): DiOfficeState {
  if (collection === null) return state;
  const base = state.missions.missions;
  const missions = collection === 'small' ? base.filter((m) => smallRefs !== null && smallRefs.includes(m.missionRef)) : [...base, ...base.map((m) => ({ ...m, missionRef: `${m.missionRef}#b`, nativeId: `${m.nativeId}#b` })), ...base.map((m) => ({ ...m, missionRef: `${m.missionRef}#c`, nativeId: `${m.nativeId}#c` }))];
  return { ...state, missions: { ...state.missions, missions } };
}
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

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
          return;
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
    console.warn(`SKIPPED (K1 keyboard ownership): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  collection = null;
}, STEPS_MS);


const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
const record = (name: string, r: Record<string, unknown>) => console.log(`B3RT ${name}: ${JSON.stringify(r)}`);
const SHOTS = process.env.DI_B3RT_SHOTS ?? null;
/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B3RT SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
}

async function boot(width: number, height: number, opts: { wasReset?: boolean } = {}): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(shaped(office)));
  // wasReset: the server reporting a layout reset, which shows the (upstream) migration notice over the world
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis), ...(opts.wasReset === true ? { wasReset: true } : {}) }));
  await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, {
    timeout: 60_000,
  });
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}


const SIZES = [
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
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

async function pressesUntil(target: string, key: 'Tab' | 'Shift+Tab', limit = 400): Promise<number | null> {
  for (let i = 1; i <= limit; i += 1) {
    await p().keyboard.press(key);
    if ((await focusNow()) === target) return i;
  }
  return null;
}
const counts = () => p().evaluate(() => ({ rows: document.querySelectorAll('[data-testid="di-scan-row"]').length, orchestrators: document.querySelectorAll('[data-testid="di-rail-orchestrator"]').length }));
/** SETUP (compose-3: the first version trimmed to the first four Missions IN THE STATE, none of which is a row, so boot never saw a row): learn which four the page draws as rows. */
async function ensureSmall(vp: { width: number; height: number }): Promise<void> {
  if (smallRefs !== null) return;
  collection = null;
  await boot(vp.width, vp.height);
  smallRefs = await p().evaluate(() => Array.from(document.querySelectorAll('[data-testid="di-scan-row"]')).slice(0, 4).map((r) => r.getAttribute('data-mission-ref') ?? ''));
  assert.equal(smallRefs.length, 4, 'SETUP: four rows were found to keep');
  assert.ok(smallRefs.every((ref) => ref !== ''), 'SETUP: each kept row carries its Mission reference');
}
/** The forward route from Home's search to the Map, by the jump, from the DEFAULT state (the world controls' disclosure closed). */
async function forwardCost(vp: { width: number; height: number }, size: Collection) {
  if (size === 'small') await ensureSmall(vp);
  collection = size;
  await boot(vp.width, vp.height);
  const shape = await counts();
  const disclosureClosedAtStart = (await p().locator(sel('world-view-map')).count()) === 0;
  await p().locator(sel('di-scan-search')).focus();
  const toJump = await pressesUntil('di-jump-world', 'Tab', 12);
  const seenFocused = toJump === null ? null : await seen(sel('di-jump-world'));
  if (toJump !== null) await p().keyboard.press('Enter');
  await p().waitForTimeout(250);
  return { shape, disclosureClosedAtStart, toJump, seenFocused, landed: await focusNow(), expanded: await p().locator(sel('di-world-disclosure')).getAttribute('aria-expanded') };
}
/** The PLAIN forward route (what the page cost before): the disclosure opened by hand, then Tab from the search on past every row to the Map's toggle. */
async function plainForward(vp: { width: number; height: number }, size: Collection) {
  if (size === 'small') await ensureSmall(vp);
  collection = size;
  await boot(vp.width, vp.height);
  await p().click(sel('di-world-disclosure'));
  await p().waitForSelector(sel('world-view-map'));
  await p().locator(sel('di-scan-search')).focus();
  return { shape: await counts(), presses: await pressesUntil('world-view-map', 'Tab') };
}
async function backCost(vp: { width: number; height: number }, size: Collection) {
  if (size === 'small') await ensureSmall(vp);
  collection = size;
  await boot(vp.width, vp.height);
  await p().click(sel('di-world-disclosure'));
  await p().waitForSelector(sel('world-view-map'));
  await p().locator(sel('world-view-map')).focus();
  const toJump = await pressesUntil('di-jump-missions', 'Tab', 12);
  const seenFocused = toJump === null ? null : await seen(sel('di-jump-missions'));
  if (toJump !== null) await p().keyboard.press('Enter');
  await p().waitForTimeout(150);
  const jumped = await focusNow();
  // the PLAIN route back (what the page cost before): Tab on from the Map's toggle round the end of the page to Home's search
  await p().locator(sel('world-view-map')).focus();
  const plain = await pressesUntil('di-scan-search', 'Tab');
  return { shape: await counts(), toJump, seenFocused, landed: jumped, plain };
}
for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 ROUTES, Map, at ${where}, FORWARD: Home to the Map by the jump costs the same presses with 4, 64 and 192 Missions, and opens the closed disclosure`, async () => {
    const small = await forwardCost(vp, 'small');
    const base = await forwardCost(vp, null);
    const large = await forwardCost(vp, 'large');
    const plainSmall = await plainForward(vp, 'small');
    const plainLarge = await plainForward(vp, 'large');
    record(`FORWARD ${vp.width}x${vp.height}`, { small, base, large, plainSmall, plainLarge });
    assert.ok(small.shape.rows < base.shape.rows && base.shape.rows < large.shape.rows, `CALIBRATION: the collections differ in rows (${small.shape.rows}, ${base.shape.rows}, ${large.shape.rows})`);
    assert.ok(plainSmall.presses !== null && plainLarge.presses !== null && plainLarge.presses > plainSmall.presses, `CALIBRATION: the plain route GROWS with the collection (${plainSmall.presses} to ${plainLarge.presses} presses)`);
    for (const r of [small, base, large]) {
      assert.equal(r.disclosureClosedAtStart, true, 'STATE: the world controls start behind the closed disclosure');
      assert.ok(r.toJump !== null, `CONTRACT (REACH): the jump is reached by sequential Tab from the search (${r.shape.rows} rows)`);
      assert.equal(r.landed, 'world-view-map', `CONTRACT (LANDS): Enter opens the disclosure and lands on the Map's toggle (${r.shape.rows} rows)`);
      assert.equal(r.expanded, 'true', 'CONTRACT (OPENS): the disclosure reads expanded');
    }
    assert.deepEqual([small.toJump, base.toJump], [large.toJump, large.toJump], `CONTRACT (INVARIANT): the jump is the same number of Tabs from the search with ${small.shape.rows}, ${base.shape.rows} and ${large.shape.rows} rows`);
  });
  test(`B3 ROUTES, Map, at ${where}, BACK: from the Map's toggle to Home's search by the mirror jump costs the same presses with 4, 64 and 192 Missions`, async () => {
    const small = await backCost(vp, 'small');
    const base = await backCost(vp, null);
    const large = await backCost(vp, 'large');
    record(`BACK ${vp.width}x${vp.height}`, { small, base, large });
    assert.ok(small.plain !== null && large.plain !== null && large.plain > small.plain, `CALIBRATION: the plain route back GROWS with the collection (${small.plain} to ${large.plain} presses)`);
    for (const r of [small, base, large]) {
      assert.ok(r.toJump !== null, `CONTRACT (REACH): the mirror jump is reached by sequential Tab from the Map's toggle (${r.shape.rows} rows)`);
      assert.equal(r.landed, 'di-scan-search', `CONTRACT (LANDS): Enter lands on Home's search (${r.shape.rows} rows)`);
    }
    assert.deepEqual([small.toJump, base.toJump], [large.toJump, large.toJump], 'CONTRACT (INVARIANT): the way back is the same number of Tabs whatever the collection');
  });
  test(`B3 ROUTES, Map, at ${where}, SEEN: each jump is hidden until focused and then genuinely visible`, async () => {
    const forward = await forwardCost(vp, null);
    await calibrateSeen();
    const hiddenBefore = await seen(sel('di-jump-world'));
    const back = await backCost(vp, null);
    record(`JUMP-SEEN ${vp.width}x${vp.height}`, { hiddenBefore, forward: forward.seenFocused, back: back.seenFocused });
    await shot(`b3rt-jump-${vp.width}x${vp.height}.png`);
    assert.equal(forward.seenFocused?.seen, true, `CONTRACT (SEEN): "Go to the Map", focused, is seen (${forward.seenFocused?.why.join('; ') ?? 'not reached'})`);
    assert.equal(back.seenFocused?.seen, true, `CONTRACT (SEEN): "Back to the Mission list", focused, is seen (${back.seenFocused?.why.join('; ') ?? 'not reached'})`);
    assert.equal(hiddenBefore.seen, false, 'CONTRACT (HIDDEN): unfocused, the jump is not drawn');
  });
}
// ── the tray: "Go to minimized surfaces (n)" ─────────────────────────────────────────────────────────────────────────────────────────────────────
async function minimizeWorkspace(): Promise<void> {
  // composed: Minimize is in the workspace's Options menu; on the canonical bytes it is a direct control
  if ((await p().locator(sel('di-mr-options')).count()) > 0) {
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel('di-mr-minimize'));
  }
  await p().click(sel('di-mr-minimize'));
  await p().waitForSelector(sel('di-mr-restore'), { timeout: 10_000 });
}
async function openWorkspace(): Promise<void> {
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
}
const jumpLabel = () => p().locator(sel('di-jump-minimized')).evaluate((el) => (el.textContent ?? '').trim()).catch(() => null);
async function trayCost(vp: { width: number; height: number }, size: Collection) {
  if (size === 'small') await ensureSmall(vp);
  collection = size;
  await boot(vp.width, vp.height);
  const absentBefore = (await p().locator(sel('di-jump-minimized')).count()) === 0;
  await openWorkspace();
  await minimizeWorkspace();
  const chips = await p().locator(sel('di-minimized-tray')).evaluate((el) => el.childElementCount);
  const label = await jumpLabel();
  await p().locator(sel('di-scan-search')).focus();
  const toJump = await pressesUntil('di-jump-minimized', 'Tab', 12);
  const seenFocused = toJump === null ? null : await seen(sel('di-jump-minimized'));
  if (toJump !== null) await p().keyboard.press('Enter');
  await p().waitForTimeout(150);
  return { shape: await counts(), absentBefore, chips, label, toJump, seenFocused, landed: await focusNow() };
}
for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 ROUTES, tray, at ${where}: Home to the minimized surface by the jump costs the same presses with 4, 64 and 192 Missions, lands on the restore control, and its count tracks`, async () => {
    const small = await trayCost(vp, 'small');
    const base = await trayCost(vp, null);
    const large = await trayCost(vp, 'large');
    await calibrateSeen();
    record(`TRAY ${vp.width}x${vp.height}`, { small, base, large });
    await shot(`b3rt-tray-${vp.width}x${vp.height}.png`);
    for (const r of [small, base, large]) {
      assert.equal(r.absentBefore, true, 'CONTRACT (ONLY WHERE IT CAN ACT): with nothing minimized there is no jump to the tray');
      assert.equal(r.chips, 1, 'STATE: one surface is minimized (one chip in the tray)');
      assert.equal(r.label, 'Go to minimized surfaces (1)', `CONTRACT (COUNT): the label carries the count, and the count is the chips in the tray (${r.label})`);
      assert.ok(r.toJump !== null, `CONTRACT (REACH): the jump is reached by sequential Tab from the search (${r.shape.rows} rows)`);
      assert.equal(r.landed, 'di-mr-restore', `CONTRACT (LANDS): Enter lands on the minimized surface's restore control (${r.shape.rows} rows)`);
      assert.equal(r.seenFocused?.seen, true, `CONTRACT (SEEN): focused, the jump is seen (${r.seenFocused?.why.join('; ') ?? 'not reached'})`);
    }
    assert.ok(small.shape.rows < base.shape.rows && base.shape.rows < large.shape.rows, `CALIBRATION: the collections differ in rows (${small.shape.rows}, ${base.shape.rows}, ${large.shape.rows})`);
    assert.deepEqual([small.toJump, base.toJump], [large.toJump, large.toJump], 'CONTRACT (INVARIANT): the tray jump is the same number of Tabs whatever the collection');
  });
  test(`B3 ROUTES, tray, at ${where}: the count is never stale, and the strip is still LATER in the DOM than the dock (the one-pane fix depends on it)`, async () => {
    await boot(vp.width, vp.height);
    await openWorkspace();
    await minimizeWorkspace();
    const first = await jumpLabel();
    // restore it: the jump goes away; minimize again: it is back at 1 (not a stale 0 or 2)
    await p().click(sel('di-mr-restore'));
    await p().waitForSelector('.di-mr:not([hidden])');
    await p().waitForTimeout(200);
    const afterRestore = await p().locator(sel('di-jump-minimized')).count();
    await minimizeWorkspace();
    const again = await jumpLabel();
    const order = await p().evaluate(() => {
      const tray = document.querySelector('[data-testid="di-minimized-tray"]')!;
      const dock = document.querySelector('[data-testid="di-office-panel"]')!;
      return { trayFollowsDock: (dock.compareDocumentPosition(tray) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 };
    });
    record(`TRAY-COUNT ${vp.width}x${vp.height}`, { first, afterRestore, again, order, twoSurfaces: 'not reachable at Home: only the request workspace can be minimized while the office panel is open on the scan face (diB3TrayRestoreBrowser SURFACES); the count is the tray children (App.tsx minimizedCount)' });
    assert.equal(first, 'Go to minimized surfaces (1)', 'CONTRACT (COUNT): one minimized, one counted');
    assert.equal(afterRestore, 0, 'CONTRACT (COUNT): restored, the jump is gone (it is not a control that does nothing)');
    assert.equal(again, 'Go to minimized surfaces (1)', 'CONTRACT (COUNT): minimized again, it says 1 again');
    assert.equal(order.trayFollowsDock, true, 'CONTRACT (DOM ORDER): the minimized strip is LATER in the DOM than the dock; nothing here reordered it');
  });
}
// ── CONFORMANCE (rendered): the 16 detector advisories were moved onto the DECLARED ramp. Lint does not turn on advisories, so the design bar is a RENDERED read of what the page draws, at Home and in the rail. ──
const RAMP_PX = [11, 12, 13, 14, 18];
const RADII = new Set(['0px', '6px', '8px', '10px', '12px', '999px', '50%']);
const CONFORMANCE_SELECTORS = ['.di-scan-title', '.di-scan-context', '.di-scan-pill', '.di-scan-badge', '.di-rail-item', '.di-rail-new', '.di-rail-wordmark-world'];
test('B3 CONFORMANCE (rendered, Home and rail), at 1440x900: the named elements draw type sizes and radii that are on the declared ramp, and the check can fail', async () => {
  await boot(1440, 900);
  const read = await p().evaluate((selectors) => {
    const one = (el: Element) => ({ fontPx: parseFloat(getComputedStyle(el).fontSize), radius: getComputedStyle(el).borderTopLeftRadius });
    const out: Record<string, Array<{ fontPx: number; radius: string }>> = {};
    for (const sel of selectors) out[sel] = Array.from(document.querySelectorAll(sel)).slice(0, 40).map(one);
    // CONTROL: a probe drawn with the OFF-ramp values the advisories named must be reported off the ramp
    const probe = document.createElement('i');
    probe.style.cssText = 'position:fixed;left:-99px;top:0;font-size:15px;border-radius:7px';
    document.body.append(probe);
    const control = one(probe);
    probe.remove();
    return { out, control };
  }, CONFORMANCE_SELECTORS);
  const offRamp = (v: { fontPx: number; radius: string }) => [!RAMP_PX.includes(v.fontPx) ? `font ${v.fontPx}px` : null, !RADII.has(v.radius) ? `radius ${v.radius}` : null].filter((x) => x !== null);
  record('CONFORMANCE', { counts: Object.fromEntries(Object.entries(read.out).map(([k, v]) => [k, v.length])), control: read.control, offRamp: Object.fromEntries(Object.entries(read.out).map(([k, v]) => [k, [...new Set(v.flatMap(offRamp))]])) });
  assert.deepEqual(offRamp(read.control), ['font 15px', 'radius 7px'], 'CALIBRATION: the check names an off-ramp size and radius');
  for (const [selector, values] of Object.entries(read.out)) {
    if (values.length === 0) continue;
    assert.deepEqual([...new Set(values.flatMap(offRamp))], [], `CONTRACT (RAMP): ${selector} (${values.length} drawn) is on the declared ramp`);
  }
  assert.ok(Object.values(read.out).filter((v) => v.length > 0).length >= 5, `STATE: at least five of the seven selectors are drawn at Home and in the rail (${Object.entries(read.out).map(([k, v]) => `${k} ${v.length}`).join(', ')})`);
});

// ── BACK TO THE MISSION LIST, WITH THE OFFICE MINIMIZED (executor1, 2026-11-08; the exact trigger reviewer1 names: MINIMIZE THE OFFICE WITH THE WORLD CONTROLS OPEN, THEN ACTIVATE BACK) ──────────────────────────────────────
// The complete predicate: the destination is the INTENDED one (Home's search), it is SEEN, and the state the route was meant to change HAS changed (the list is exposed). Seen alone passed a no-op: focus began on a seen control and stayed there.
const BACK_SIZES = [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
] as const;
for (const vp of BACK_SIZES) {
  test(`B3 ROUTE, Back to the Mission list with the office MINIMIZED and the world controls open, at ${vp.width}x${vp.height}: the list is EXPOSED, focus MOVES to Home's search, and the list is as it was left`, async () => {
    collection = 'large';
    await boot(vp.width, vp.height);
    collection = null;
    await calibrateSeen();
    await p().click(sel('di-world-disclosure'));
    await p().waitForSelector(sel('world-view-map'));
    // what the list holds, to be retained: it is scrolled
    const scrolled = await p().evaluate(() => {
      const aside = document.querySelector<HTMLElement>('[data-testid="di-office-panel"]');
      if (aside === null) return null;
      const scroller = [aside, ...aside.querySelectorAll<HTMLElement>('*')].find((e) => e.scrollHeight > e.clientHeight + 200 && /(auto|scroll)/.test(getComputedStyle(e).overflowY)) ?? null;
      if (scroller === null) return null;
      scroller.setAttribute('data-b3-list-scroller', '1');
      scroller.scrollTop = 300;
      return scroller.scrollTop;
    });
    assert.ok(scrolled !== null && scrolled > 0, `STATE: the Mission list is scrollable and scrolled (${String(scrolled)})`);
    await p().click(sel('di-office-panel-minimize'));
    await p().waitForSelector(sel('di-office-panel-collapsed'), { timeout: 10_000 });
    const entry = {
      asideHidden: await p().evaluate(() => document.querySelector('[data-testid="di-office-panel"]')?.hasAttribute('hidden') ?? null),
      searchMounted: (await p().locator(sel('di-scan-search')).count()) > 0,
      searchSeen: (await seen(sel('di-scan-search'))).seen,
      chipSeen: (await seen(sel('di-office-panel-collapsed'))).seen,
      worldControlsSeen: (await seen(sel('world-view-map'))).seen,
    };
    assert.deepEqual(entry, { asideHidden: true, searchMounted: true, searchSeen: false, chipSeen: true, worldControlsSeen: true }, 'ENTRY: the office is minimized with the world controls open; the search is MOUNTED and NOT SEEN');
    await p().locator(sel('di-jump-missions')).focus();
    const startedOn = await focusNow();
    await p().keyboard.press('Enter');
    await p().waitForTimeout(400);
    const landed = await focusNow();
    const after = await p().evaluate(() => ({
      asideHidden: document.querySelector('[data-testid="di-office-panel"]')?.hasAttribute('hidden') ?? null,
      chipPresent: document.querySelector('[data-testid="di-office-panel-collapsed"]') !== null,
      listScroll: document.querySelector<HTMLElement>('[data-b3-list-scroller="1"]')?.scrollTop ?? null,
    }));
    const problems: string[] = [];
    if (startedOn !== 'di-jump-missions') problems.push(`STATE: the route was fired from ${startedOn}, not from the jump`);
    if (after.asideHidden !== false) problems.push(`EXPOSED: the office panel is still hidden (asideHidden ${String(after.asideHidden)}): the Mission list is not exposed`);
    if (after.chipPresent) problems.push('EXPOSED: the Restore chip is still drawn: the panel is still minimized');
    const row = await seen(sel('di-scan-row'));
    if (!row.seen) problems.push(`EXPOSED: the first Mission row is NOT SEEN (${row.why.join('; ')})`);
    // rows COUNTED: how many Mission rows are actually drawn in the window and hit-testable at their own centres (a list that is mounted and hidden draws none)
    const rowsDrawn = await p().evaluate(() => [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-row"]')].filter((el) => { const r = el.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.top >= window.innerHeight) return false; const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return top === el || el.contains(top); }).length);
    if (rowsDrawn < 3) problems.push(`EXPOSED: only ${rowsDrawn} Mission rows are drawn and hit-testable (a list that is exposed draws several)`);
    if (landed === startedOn) problems.push(`MOVED: focus never left the control it started on (${landed})`);
    if (landed !== 'di-scan-search') problems.push(`DESTINATION: focus is on ${landed}, not on Home's search`);
    const searchSeen = await seen(sel('di-scan-search'));
    if (!searchSeen.seen) problems.push(`SEEN: Home's search is NOT SEEN (${searchSeen.why.join('; ')})`);
    if (after.listScroll !== scrolled) problems.push(`RETAINED: the list's scroll was ${scrolled} and is ${String(after.listScroll)}`);
    if (!(await seen(sel('world-view-map'))).seen) problems.push('RETAINED: the world controls the route was invoked from are no longer seen');
    record(`BACK-MINIMIZED ${vp.width}x${vp.height}`, { entry, startedOn, landed, after, scrolled, rowsDrawn, problems });
    await shot(`b3rt-back-minimized-${vp.width}x${vp.height}.png`);
    assert.deepEqual(problems, [], `CONTRACT (BACK, MINIMIZED): at ${vp.width}x${vp.height} the route exposes the list, moves focus to Home's search (seen) and keeps the list as it was left`);
  });
}


// ── BACK TO THE MISSION LIST, WITH A MISSION OPEN (executor1, 2026-11-08; the second branch the Lead named: a covering screen). The route's promise is the same: ACTUAL LIST EXPOSURE, a meaningful SEEN landing in it, and retained state where the two are compatible.
// Two-pane sizes (1440, 1600): the list is BESIDE the screen, so Back lands in its search and the screen is KEPT. The single-pane size (1280): the screen COVERS the list (its search is mounted and visibility:hidden), so the route to the list is Home, activated: the covering screen goes, the list is exposed, focus lands in its search.
for (const vp of BACK_SIZES) {
  test(`B3 ROUTE, Back to the Mission list with a Mission OPEN and the office minimized then expanded, at ${vp.width}x${vp.height}: the list is EXPOSED, focus MOVES to Home's search (seen), and the screen is kept where the list sits beside it`, async () => {
    await boot(vp.width, vp.height);
    await calibrateSeen();
    await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await p().click(sel('di-office-panel-minimize'));
    await p().waitForSelector(sel('di-office-panel-collapsed'), { timeout: 10_000 });
    await p().click(sel('di-office-panel-collapsed'));
    await p().waitForSelector(sel('di-office-panel-collapsed'), { state: 'detached', timeout: 10_000 });
    await p().locator(sel('di-world-disclosure')).focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('world-view-map'), { state: 'attached', timeout: 10_000 });
    const before = {
      stageView: await p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null),
      searchSeen: (await seen(sel('di-scan-search'))).seen,
    };
    await p().locator(sel('di-jump-missions')).focus();
    const startedOn = await focusNow();
    await p().keyboard.press('Enter');
    await p().waitForTimeout(500);
    const landed = await focusNow();
    const after = {
      stageView: await p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null),
      searchSeen: (await seen(sel('di-scan-search'))).seen,
      rowsDrawn: await p().evaluate(() => [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-row"]')].filter((el) => { const r = el.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.top >= window.innerHeight) return false; const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return top === el || el.contains(top); }).length),
    };
    const problems: string[] = [];
    if (startedOn !== 'di-jump-missions') problems.push(`STATE: the route was fired from ${startedOn}, not from the jump`);
    if (!after.searchSeen) problems.push('EXPOSED: Home\'s search is NOT SEEN: the Mission list is not exposed');
    if (after.rowsDrawn < 3) problems.push(`EXPOSED: only ${after.rowsDrawn} Mission rows are drawn and hit-testable`);
    if (landed === startedOn) problems.push(`MOVED: focus never left the control it started on (${landed})`);
    if (landed !== 'di-scan-search') problems.push(`DESTINATION: focus is on ${landed}, not on Home's search`);
    // RETAINED where compatible: when the list was already beside the screen (two panes) the screen stays; when it covered the list (one pane) it had to go
    if (before.searchSeen && after.stageView !== before.stageView) problems.push(`RETAINED: the list was already beside the screen, yet the screen changed from ${before.stageView} to ${after.stageView}`);
    record(`BACK-MISSION-OPEN ${vp.width}x${vp.height}`, { before, startedOn, landed, after, problems });
    await shot(`b3rt-back-mission-open-${vp.width}x${vp.height}.png`);
    assert.deepEqual(problems, [], `CONTRACT (BACK, MISSION OPEN): at ${vp.width}x${vp.height} the route exposes the list, moves focus to Home's search (seen), and keeps the screen where the list sits beside it`);
  });
}
