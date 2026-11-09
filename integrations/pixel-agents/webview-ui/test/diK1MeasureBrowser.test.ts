/**
 * K1, MEASUREMENT ONLY (the Lead granted M1-M4, 2026-11-05): where do keys go while the Map is OPEN, and which predicate fits the evidence?
 * NOT attribution: the K1 attribution is the matched pair mapkeys-measure-HEAD-chrome.log / mapkeys-measure-LIVE-chrome.log, never re-run into.
 * Chrome, the real App (test/harness/di-app-scan.html), the session-mismatch v2 FIXTURE (available capacity), the large world
 * (packaging/di/worlds/living-metropolis.json), the request panel SYNTHETIC. Nothing is live.
 *
 * ASSERTED, only to know each state is the named one (the Map open and drawn, a screen open or not, the panel minimized or not). Everything else is
 * RECORDED, printed as "K1 <case>: {...}", and asserted NOT AT ALL:
 *  - M1 (the FRAME): two panes, the Map OPEN and UNCOVERED, NO screen, focus in Home's search. A POSITIVE CONTROL first, the Map CLOSED: Cmd+N (a
 *    real application binding, DiScanPanel) opens New Mission. Then the same with the Map open: does Cmd+N fire, do the page's key handlers receive
 *    a key, does typing reach the search box, and what does Escape close?
 *  - M2 (the SIGNAL): a Mission docked, the office panel MINIMIZED, then the Map opened: is `.di-scan` hidden while `data-stage` still says open,
 *    and where do keys go (the Map is the foreground surface here)?
 *  - M3 (a SECOND ROUTE): about 1000 px wide, the Map open, then New Mission and its request workspace (COVERING at this width): does the Map stay
 *    drawn beneath, and do the workspace's keys reach it?
 *  - M4 (the BASELINE for before / after): the Map open beneath a docked Mission, at 1440 and 1280: does Cmd+N fire, do the handlers receive keys?
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';
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
    console.warn(`SKIPPED (K1 measurement): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const mapDrawn = async () => (await p().locator(sel('world-map')).count()) === 1;
const screenView = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);

async function boot(width: number, height: number): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
  await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, {
    timeout: 60_000,
  });
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}
/** Open the Map as a person does: the scan face's world controls are collapsible and drawn only while shown. */
async function openMap(): Promise<void> {
  if ((await p().locator(sel('world-view-map')).count()) === 0) await p().click(sel('di-world-disclosure'));
  await p().click(sel('world-view-map'));
  await p().waitForSelector(sel('world-map'));
}
/** Do the page's own key handlers receive a key at all? (a window listener, bubble phase, counting) */
async function handlersReceiveKeys(): Promise<boolean> {
  await p().evaluate(() => {
    if (document.documentElement.dataset.keysSeen === undefined) {
      document.documentElement.dataset.keysSeen = '0';
      window.addEventListener('keydown', () => {
        document.documentElement.dataset.keysSeen = String(Number(document.documentElement.dataset.keysSeen) + 1);
      });
    }
  });
  const read = () => p().evaluate(() => Number(document.documentElement.dataset.keysSeen));
  const before = await read();
  await p().keyboard.press('ArrowDown');
  return (await read()) > before;
}
/** Cmd+N, the application binding: did New Mission open? */
async function cmdN(): Promise<string | null> {
  await p().keyboard.press('Meta+n');
  await p().waitForTimeout(400);
  return screenView();
}
const record = (name: string, r: Record<string, unknown>) => console.log(`K1 ${name}: ${JSON.stringify(r)}`);

test('M1 (the FRAME): two panes, the Map OPEN and UNCOVERED, no screen, focus in Home; with a POSITIVE CONTROL (the Map closed) first', async () => {
  // POSITIVE CONTROL: the Map closed, focus in Home's search, Cmd+N opens New Mission (so a "no" below is about the Map, not about the binding)
  await boot(1440, 900);
  await p().locator(sel('di-scan-search')).focus();
  expect(await screenView(), 'no screen open').toBe(null);
  const control = { focus: await focusNow(), cmdN: await cmdN() };
  record('M1 CONTROL (Map closed)', control);

  await page?.close();
  await boot(1440, 900);
  await openMap();
  expect(await mapDrawn(), 'the Map is open and drawn').toBe(true);
  expect(await screenView(), 'no screen open: nothing covers the Map').toBe(null);
  await p().locator(sel('di-scan-search')).focus();
  const r: Record<string, unknown> = { focus: await focusNow(), homeCovered: await p().locator(sel('di-scan-home')).getAttribute('data-covered') };
  r.pageHandlersReceiveKeys = await handlersReceiveKeys();
  await p().keyboard.type('ab');
  r.typedIntoSearch = await p().locator(sel('di-scan-search')).inputValue();
  r.cmdN = await cmdN();
  if ((await screenView()) !== null) {
    record('M1 (Map open, focus in Home)', r);
    return;
  }
  await p().locator(sel('di-scan-search')).focus();
  await p().keyboard.press('Escape');
  await p().waitForTimeout(300);
  r.escape = { mapDrawn: await mapDrawn(), focus: await focusNow() };
  record('M1 (Map open, focus in Home)', r);
}, STEPS_MS);

test('M2 (the SIGNAL): a Mission docked, the office panel MINIMIZED, then the Map: is data-stage still "open" on a hidden panel, and where do keys go?', async () => {
  await boot(1440, 900);
  await p().click(sel('di-scan-row'));
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click(sel('di-office-panel-minimize'));
  await p().waitForTimeout(300);
  const aside = await p().evaluate(() => {
    const a = document.querySelector('.di-scan');
    return { hidden: a instanceof HTMLElement ? a.hidden : null, dataStage: a?.getAttribute('data-stage') ?? null, stageInDom: document.querySelector('[data-testid="di-scan-stage"]') !== null };
  });
  expect(aside.hidden, 'the office panel is minimized (its aside hidden)').toBe(true);
  await openMap();
  expect(await mapDrawn(), 'the Map is open and drawn, in front of the world').toBe(true);
  const r: Record<string, unknown> = { ...aside, stageVisible: await p().evaluate(() => (document.querySelector('[data-testid="di-scan-stage"]') as HTMLElement | null)?.checkVisibility() ?? null) };
  r.pageHandlersReceiveKeys = await handlersReceiveKeys();
  await p().keyboard.press('Escape');
  await p().waitForTimeout(300);
  r.escapeClosedTheMap = !(await mapDrawn());
  record('M2 (minimized with a screen open, then the Map)', r);
}, STEPS_MS);

test('M3 (a SECOND ROUTE): about 1000 px, the Map open, then New Mission and its request workspace, COVERING at this width', async () => {
  await boot(1000, 800);
  await openMap();
  expect(await mapDrawn(), 'the Map is open and drawn').toBe(true);
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr');
  const r: Record<string, unknown> = {
    covering: await p().locator('.di-mr').first().getAttribute('data-covering'),
    mapDrawnBeneath: await mapDrawn(),
    focus: await focusNow(),
  };
  r.pageHandlersReceiveKeys = await handlersReceiveKeys();
  await p().keyboard.press('Escape');
  await p().waitForTimeout(300);
  r.firstEscape = { mapDrawn: await mapDrawn(), workspaceOpen: (await p().locator('.di-mr').count()) > 0, view: await screenView() };
  record('M3 (a covering workspace over the open Map)', r);
}, STEPS_MS);

for (const [w, h] of [
  [1440, 900],
  [1280, 800],
] as const)
  test(`M4 (the BASELINE) at ${w}x${h}: the Map open beneath a docked Mission, focus on Back; does Cmd+N fire, do the handlers receive keys?`, async () => {
    await boot(w, h);
    await openMap();
    await p().click(sel('di-scan-row'));
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    expect(await mapDrawn(), 'the Map is open beneath the docked screen').toBe(true);
    await p().locator(sel('di-scan-back')).focus();
    const r: Record<string, unknown> = { focus: await focusNow() };
    r.pageHandlersReceiveKeys = await handlersReceiveKeys();
    r.cmdN = await cmdN();
    r.mapDrawnAfter = await mapDrawn();
    record(`M4 @${w} (Map beneath a docked Mission)`, r);
  }, STEPS_MS);

/**
 * W (the WORLD-PAN premise, the Lead: measure it before any gate rests on it): does an arrow key on a panel BUTTON pan the world (App.tsx's key-pan
 * listener)? The observable: the pan clears `cameraFollowId` on the app's own OfficeState (the harness probe exposes it). Many paths clear it, so it is
 * DISCRIMINATED: W0, no key (must stay set: nothing clears it on its own in the interval); W1, ArrowRight with focus on a Home TAB (the premise);
 * W2, ArrowRight in the SEARCH box (the pan skips form controls: must stay set); W3, the Map OPEN, ArrowRight on the tab (today's guard).
 */
test('W (the world-pan premise): does an arrow key on a panel BUTTON pan the world? With a no-key control, a form-control control and the Map open', async () => {
  await boot(1440, 900);
  const follow = () =>
    p().evaluate(() => {
      const o = (window as unknown as { __diHarnessOffice?: { cameraFollowId: number | null; characters: Map<number, unknown> } }).__diHarnessOffice!;
      const id = [...o.characters.keys()].find((k) => k >= 0) ?? null;
      o.cameraFollowId = id;
      return id;
    });
  const followNow = () =>
    p().evaluate(() => (window as unknown as { __diHarnessOffice?: { cameraFollowId: number | null } }).__diHarnessOffice!.cameraFollowId);
  const r: Record<string, unknown> = {};
  await p().locator(sel('di-scan-tab-open')).focus();
  const id = await follow();
  expect(id, 'a character to follow').not.toBe(null);
  await p().waitForTimeout(400);
  r.W0_noKey_stillFollowing = (await followNow()) === id;
  await follow();
  await p().keyboard.press('ArrowRight');
  await p().waitForTimeout(200);
  r.W1_tabButton_arrowRanTheWorldPanListener = (await followNow()) === null;
  r.W1_focus = await focusNow();
  await p().locator(sel('di-scan-search')).focus();
  await follow();
  await p().keyboard.press('ArrowRight');
  await p().waitForTimeout(200);
  r.W2_searchBox_arrowRanTheWorldPanListener = (await followNow()) === null;
  await openMap();
  expect(await mapDrawn(), 'the Map is open').toBe(true);
  await p().locator(sel('di-scan-tab-open')).focus();
  await follow();
  await p().keyboard.press('ArrowRight');
  await p().waitForTimeout(200);
  r.W3_mapOpen_tabButton_arrowRanTheWorldPanListener = (await followNow()) === null;
  record('W (the world-pan premise)', r);
}, STEPS_MS);
