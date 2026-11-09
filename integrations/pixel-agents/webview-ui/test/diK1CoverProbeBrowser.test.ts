/**
 * K1, MEASUREMENT (the Lead's grant (iii); split by the Lead's ruling into ONE INSTRUMENT PER PREDICATE QUESTION), RENDERED in Chrome (not Brave
 * evidence): what is over the open Map's box in each K1 state. It closes the bound of the STATIC sweep of covering surfaces (k1-record.md, section
 * (3)), which finds only covers stated as fixed or absolute-inset-0 in source. Same harness and fixtures as diK1KeyboardOwnershipBrowser (the real
 * App, the session-mismatch v2 FIXTURE, the living metropolis, the request panel SYNTHETIC; nothing is live).
 *
 * (C) is a question of VISUAL COVERAGE. Its instrument is a 9 x 9 grid inside the Map's box: the topmost BOX in hit-test order at each point
 * (`elementFromPoint`), with pointer-events forced on for the reading only (hit-testing skips pointer-events:none, which would blind it to exactly
 * the covers it hunts; removed straight after). That reading is a SUPERSET OF PAINT (an empty, transparent layout box counts) and it is NOT POINTER
 * REACHABILITY. ASSERTED in every state, before anything else: (C)'s own answer (WorldMap.tsx `mapCoveredNow`'s rule, read at the same moment) is the
 * expected one, and it AGREES with the grid (covered exactly when every point is a stated cover). The box classes are printed for the reader:
 * descriptive, not asserted.
 *
 * (F) is a question of FOCUS. Its instrument enumerates, from MARKUP, every element that can TAKE FOCUS (not disabled, not inert, visible) whose box
 * meets the Map's box (an unclassified one is printed with its ANCESTRY, so it is identified by what it is). Each is classified by structure, and (F)'s own rule (WorldMap.tsx `focusOwnedOutsideMap`) is applied to it. ASSERTED: none is
 * left without a class, and (F)'s answer is the RULED one for its class. The Map and its controls (`[data-di-world-controls]`) keep the keys; world
 * content (an agent's Mission label, the Lead's ruling: it opens a Mission, it does not operate the Map), panels and stated covers own them.
 *
 * METHOD BOUND (the Lead's ruling): the (F) list is a SNAPSHOT, never a complete inventory of what can hold focus over the Map. The agents' Mission
 * labels move with their agents, and the list differed between probe v1, the label run and probe v2. So the per-state ruling comes from the
 * PREDICATE and the FUNCTION of each control, not from one enumeration.
 *
 * POSITIVE CONTROLS, so each instrument can see what it reports: the grid sees the Map (uncovered) and a stated cover (docked); the focus list sees a
 * control of the Map's own region (uncovered).
 */
import assert from 'node:assert/strict';
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
import { relayReply, syntheticOverview } from './diNewMissionSynthetic.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const metropolis = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', syntheticOverview())));
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
    console.warn(`SKIPPED (K1 cover probe): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const record = (name: string, r: Record<string, unknown>) => console.log(`K1PROBE ${name}: ${JSON.stringify(r)}`);

async function boot(width: number, height: number, opts: { wasReset?: boolean } = {}): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  // wasReset: the server reporting a layout reset, which shows the (upstream) migration notice over the world
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis), ...(opts.wasReset === true ? { wasReset: true } : {}) }));
  await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, {
    timeout: 60_000,
  });
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}
async function openMap(): Promise<void> {
  if ((await p().locator(sel('world-view-map')).count()) === 0) await p().click(sel('di-world-disclosure'));
  await p().click(sel('world-view-map'));
  await p().waitForSelector(sel('world-map'));
}
async function dockFirst(): Promise<void> {
  await p().click(sel('di-scan-row'));
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}
const settle = () => p().waitForTimeout(300);

type Focusable = { what: string; testid: string | null; tag: string; yields: boolean; ancestry: string | null };
type Probe = {
  state: string;
  box: { left: number; top: number; right: number; bottom: number };
  coverNow: boolean;
  allStated: boolean;
  boxClasses: Record<string, number>;
  focusables: Focusable[];
};

async function probe(state: string): Promise<Probe> {
  const r = await p().evaluate(() => {
    const host = document.querySelector<HTMLElement>('[data-testid="world-map"]');
    if (host === null) throw new Error('the Map is not drawn: nothing to probe');
    const m = host.getBoundingClientRect();
    const N = 9;
    // (C): the box grid, pointer-events forced on for the reading only
    const BOX_NAMES: Array<[string, string]> = [
      ['[data-di-covers-world]', 'stated cover'],
      ['.di-rv-backdrop', 'front claim: code review'],
      ['.di-op-inspector-backdrop', 'front claim: inspector or Reception'],
      ['[data-di-world-controls]', "the Map's own region: the world controls"],
      ['[data-testid="di-world-rail-frame"]', "the world's rail frame (layout box)"],
      ['[data-testid="agent-overlay"]', 'world content: the agent overlay'],
      ['.di-mr', 'beside: the request workspace'],
      ['.di-scan', 'beside: the scan panel'],
      ['.di-op', 'beside: the office panel'],
      ['.di-min-tray', 'strip: the minimized tray'],
      ['.di-op-collapsed', 'chip: the collapsed office'],
      ['.di-rail', 'the rail'],
      ['.di-op-fixture-note', 'strip: the fixture note'],
    ];
    const force = document.createElement('style');
    force.textContent = '* { pointer-events: auto !important; }';
    document.head.append(force);
    const boxClasses: Record<string, number> = {};
    try {
      for (let i = 0; i < N; i++)
        for (let j = 0; j < N; j++) {
          const top = document.elementFromPoint(m.left + 2 + ((m.width - 4) * i) / (N - 1), m.top + 2 + ((m.height - 4) * j) / (N - 1));
          const k = top === null ? 'nothing' : host.contains(top) ? 'the Map' : (BOX_NAMES.find(([q]) => top.closest(q) !== null)?.[1] ?? 'other box');
          boxClasses[k] = (boxClasses[k] ?? 0) + 1;
        }
    } finally {
      force.remove();
    }
    const coverNow = [...document.querySelectorAll<HTMLElement>('[data-di-covers-world]')].some((el) => {
      if (!el.checkVisibility({ checkVisibilityCSS: true })) return false;
      const b = el.getBoundingClientRect();
      return b.left <= m.left + 1 && b.top <= m.top + 1 && b.right >= m.right - 1 && b.bottom >= m.bottom - 1;
    });
    // (F): every element that can take focus, from markup, whose box meets the Map's box
    const FOCUSABLE =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [contenteditable="true"], [tabindex]:not([tabindex="-1"])';
    const F_NAMES: Array<[string, string]> = [
      ['[data-di-covers-world]', 'stated cover'],
      ['.di-rv-backdrop', 'front claim: code review'],
      ['.di-op-inspector-backdrop', 'front claim: inspector or Reception'],
      ['[data-di-world-controls]', "the Map's own region: the world controls"],
      ['[data-testid="di-mission-label"]', "world content: an agent's Mission label (ruled)"],
      // RULED (the Lead), IDENTIFIED BY ANCESTRY in probe v3 (k1-coverprobe-v3-ancestry-chrome.log), not guessed: the version label ("v1.3, see what's
      // new") in the world rail's footer. It does not operate the Map, so it owns its keys and (F) yields. NAMING HAZARD: its ancestry passes through
      // `#di-world-controls`, an ID (the scan face disclosure's TARGET on the rail frame), which is NOT `[data-di-world-controls]`, the ATTRIBUTE (F)
      // reads. Two names that look the same; the label is under the ID, not the attribute.
      ['.di-version-label', 'world chrome: the version label (ruled)'],
      ['.di-mr', 'panel: the request workspace'],
      ['.di-scan', 'panel: the scan panel'],
      ['.di-op', 'panel: the office panel'],
      ['.di-min-tray', 'strip: the minimized tray'],
      ['.di-op-collapsed', 'chip: the collapsed office'],
      ['.di-rail', 'the rail'],
      ['.di-op-fixture-note', 'strip: the fixture note'],
    ];
    // the ANCESTRY of an element no class names (the Lead: settle it by what it is, never by a reading of the source)
    const chain = (el: Element) => {
      const out: string[] = [];
      for (let e: Element | null = el, i = 0; e !== null && i < 8; e = e.parentElement, i++) {
        const t = e.getAttribute('data-testid');
        const label = e === el ? ` "${(e.getAttribute('aria-label') ?? e.textContent ?? '').trim().slice(0, 40)}"` : '';
        out.push(`${e.tagName.toLowerCase()}${t ? `[${t}]` : ''}${e.id ? `#${e.id}` : ''}${e.classList.length ? '.' + [...e.classList].slice(0, 3).join('.') : ''}${label}`);
      }
      return out.join(' < ');
    };
    const meets = (b: DOMRect) => b.width > 0 && b.height > 0 && b.right > m.left && b.left < m.right && b.bottom > m.top && b.top < m.bottom;
    // (F)'s own rule, applied to an element as if it held focus (WorldMap.tsx focusOwnedOutsideMap)
    const fYields = (a: HTMLElement) => a.checkVisibility({ checkVisibilityCSS: true }) && !host.contains(a) && a.closest('[data-di-world-controls]') === null;
    const focusables = [...document.querySelectorAll<HTMLElement>(FOCUSABLE)]
      .filter((el) => el.closest('[inert]') === null && el.checkVisibility({ checkVisibilityCSS: true }) && meets(el.getBoundingClientRect()))
      .map((el) => {
        const what = host.contains(el) ? 'the Map' : (F_NAMES.find(([q]) => el.closest(q) !== null)?.[1] ?? 'UNCLASSIFIED');
        return { what, testid: el.getAttribute('data-testid'), tag: el.tagName.toLowerCase(), yields: fYields(el), ancestry: what === 'UNCLASSIFIED' ? chain(el) : null };
      });
    const round = Math.round;
    return {
      box: { left: round(m.left), top: round(m.top), right: round(m.right), bottom: round(m.bottom) },
      coverNow,
      allStated: boxClasses['stated cover'] === N * N,
      boxClasses,
      focusables,
    };
  });
  const out = { state, ...r };
  record(state, out);
  return out;
}

/** The RULED (F) answer per class: the Map and its controls keep the keys; everything else that holds focus owns its keys (the guard yields). */
const keepsKeys = (what: string) => what === 'the Map' || what === "the Map's own region: the world controls";

async function check(state: string, covered: boolean): Promise<Probe> {
  const r = await probe(state);
  // (C) FIRST, in every state: the expected answer, and agreement with the grid
  assert.equal(r.coverNow, covered, `${state}: (C) says ${covered ? 'COVERED' : 'not covered'}`);
  assert.equal(r.coverNow, r.allStated, `${state}: (C) agrees with the grid (covered exactly when every point is a stated cover)`);
  // (F): every focusable over the Map is classified, and (F)'s answer is the ruled one for its class
  assert.deepEqual(r.focusables.filter((f) => f.what === 'UNCLASSIFIED'), [], `${state}: every focusable over the Map's box has a class`);
  for (const f of r.focusables)
    assert.equal(
      f.yields,
      !keepsKeys(f.what),
      `${state}: (F) on ${f.testid ?? f.tag} (${f.what}) ${keepsKeys(f.what) ? 'keeps the keys with the Map' : 'yields: it owns its keys'}`,
    );
  return r;
}

test('UNCOVERED: two panes, Home, the Map open (POSITIVE CONTROLS: the grid sees the Map; the focus list sees a Map control)', async () => {
  await boot(1440, 900);
  await openMap();
  const r = await check('uncovered, Home @1440', false);
  assert.ok((r.boxClasses['the Map'] ?? 0) > 0, 'POSITIVE CONTROL (C): the grid sees the Map itself');
  assert.ok(r.focusables.some((f) => keepsKeys(f.what)), "POSITIVE CONTROL (F): the focus list sees a control of the Map's own region");
}, STEPS_MS);

for (const [w, h] of [
  [1440, 900],
  [1280, 800],
] as const)
  test(`DOCKED MISSION over the open Map at ${w}x${h} (POSITIVE CONTROL: the grid sees a stated cover)`, async () => {
    await boot(w, h);
    await openMap();
    await dockFirst();
    const r = await check(`docked Mission @${w}`, true);
    assert.ok((r.boxClasses['stated cover'] ?? 0) > 0, 'POSITIVE CONTROL (C): the grid sees a stated cover');
  }, STEPS_MS);

test('MINIMIZED: a Mission docked, the panel minimized (its stage hidden), the Map open', async () => {
  await boot(1440, 900);
  await dockFirst();
  await p().click(sel('di-office-panel-minimize'));
  await settle();
  assert.equal(await p().evaluate(() => (document.querySelector('.di-scan') as HTMLElement | null)?.hidden ?? null), true, 'the panel is minimized');
  await openMap();
  await check('minimized, stage hidden @1440', false);
}, STEPS_MS);

test('M3: 1000 px, the covering request workspace over the open Map', async () => {
  await boot(1000, 800);
  await openMap();
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr[data-covering="true"]');
  await check('covering workspace @1000', true);
}, STEPS_MS);

test('THE MIGRATION NOTICE over the open Map', async () => {
  await boot(1440, 900, { wasReset: true });
  await p().waitForSelector('text=We owe you an apology!');
  if ((await p().locator(sel('world-view-map')).count()) === 0) {
    await p().locator(sel('di-world-disclosure')).focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('world-view-map'));
  }
  await p().locator(sel('world-view-map')).focus();
  await p().keyboard.press('Enter');
  await p().waitForSelector(sel('world-map'));
  await check('migration notice @1440', true);
}, STEPS_MS);
