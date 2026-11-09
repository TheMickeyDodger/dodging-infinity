/**
 * K1, MEASUREMENT ONLY (the Lead: "measure with focus there before and after your change"), RENDERED in Chrome (not Brave evidence). The cover probe
 * (diK1CoverProbeBrowser) found an agent's Mission label (`di-mission-label`, a focusable button in the ToolOverlay) over the open Map's box. It sits
 * OUTSIDE the Map's region as K1 defines it (the Map host and `[data-di-world-controls]`), so K1's focus term yields to it. This measures what keys DO
 * with focus on it, in two states: Home with the Map open, and a docked Mission with the panel minimized and the Map open (where the probe saw it).
 * Run on the built K1 and on the K1 bases; the two logs are the comparison.
 *
 * Per state, MEASURED and printed, NOT asserted (they are what the two runs compare):
 *  - the rendered facts: is a visible label in view (preferably over the Map's box); is it inside `di-world`, inside `[data-di-world-controls]`, inside
 *    the Map host; is it the topmost element at its own centre WITHOUT any override (pointer-reachable); and does `di-world` contain the scan panel;
 *  - with focus on the label: does ArrowRight reach a window listener; did the world-pan LISTENER RUN (its follow reset, written before the clamp:
 *    listener execution, not motion); what Escape does: is the Map still drawn, what does the stage show before and after, where is focus.
 * ASSERTED (the instrument's own preconditions and positive control, method rule 6): focus really is on the label; a character was followed; and
 * with the Map CLOSED, the pan listener runs on the same key with focus on a label, so "did not run" can be seen as a change.
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
    console.warn(`SKIPPED (K1 label-focus measure): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const record = (name: string, r: Record<string, unknown>) => console.log(`K1LABEL ${name}: ${JSON.stringify(r)}`);

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

const sel2 = (id: string) => `[data-testid="${id}"]`;
const mapDrawn = async () => (await p().locator(sel2('world-map')).count()) === 1;
const screenView = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);
type HarnessCamera = { __diHarnessOffice?: { cameraFollowId: number | null; characters: Map<number, unknown> } };
const followSet = () =>
  p().evaluate(() => {
    const o = (window as unknown as HarnessCamera).__diHarnessOffice!;
    const id = [...o.characters.keys()].find((k) => k >= 0) ?? null;
    o.cameraFollowId = id;
    return id;
  });
const panListenerRan = () => p().evaluate(() => (window as unknown as HarnessCamera).__diHarnessOffice!.cameraFollowId === null);
const unfollow = () => p().evaluate(() => void ((window as unknown as HarnessCamera).__diHarnessOffice!.cameraFollowId = null));
const PICK = '[data-k1-measure-pick]';

/** Mark one visible, in-view label (preferring one over the Map's box) and read the rendered facts about it. */
const labelFacts = () =>
  p().evaluate(() => {
    for (const old of document.querySelectorAll('[data-k1-measure-pick]')) old.removeAttribute('data-k1-measure-pick');
    const world = document.querySelector('[data-testid="di-world"]');
    const host = document.querySelector('[data-testid="world-map"]');
    const m = host?.getBoundingClientRect() ?? null;
    const inView = (b: DOMRect) => b.width > 0 && b.height > 0 && b.left >= 0 && b.top >= 0 && b.right <= innerWidth && b.bottom <= innerHeight;
    const overMap = (b: DOMRect) => m !== null && b.left >= m.left && b.right <= m.right && b.top >= m.top && b.bottom <= m.bottom;
    const labels = [...document.querySelectorAll<HTMLElement>('[data-testid="di-mission-label"]')].filter((l) => l.checkVisibility({ checkVisibilityCSS: true }));
    const pick =
      labels.find((l) => inView(l.getBoundingClientRect()) && overMap(l.getBoundingClientRect())) ?? labels.find((l) => inView(l.getBoundingClientRect())) ?? null;
    pick?.setAttribute('data-k1-measure-pick', '');
    const b = pick?.getBoundingClientRect() ?? null;
    const top = b !== null ? document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2) : null;
    return {
      visibleLabels: labels.length,
      picked: pick !== null,
      overMapBox: b !== null ? overMap(b) : null,
      inDiWorld: pick !== null ? world?.contains(pick) === true : null,
      inWorldControls: pick !== null ? pick.closest('[data-di-world-controls]') !== null : null,
      inMapHost: pick !== null ? host?.contains(pick) === true : null,
      onTopUnforced: pick !== null ? top === pick || (top !== null && pick.contains(top)) : null,
      worldContainsScanPanel: world?.contains(document.querySelector('.di-scan')) === true,
    };
  });

/** With focus on the picked label: one ArrowRight (window listener; the pan listener's follow reset), then Escape. */
async function keysOnLabel(withEscape: boolean) {
  await p().locator(PICK).focus();
  const focused = await focusNow();
  await p().evaluate(() => {
    const d = document.documentElement.dataset;
    if (d.k1Seen === undefined) {
      d.k1Seen = '0';
      window.addEventListener('keydown', () => void (d.k1Seen = String(Number(d.k1Seen) + 1)));
    }
  });
  const seen = () => p().evaluate(() => Number(document.documentElement.dataset.k1Seen));
  const followed = await followSet();
  const s0 = await seen();
  await p().keyboard.press('ArrowRight');
  await settle();
  const windowReceived = (await seen()) > s0;
  const panRan = await panListenerRan();
  await unfollow();
  let escape: Record<string, unknown> | null = null;
  if (withEscape) {
    await p().locator(PICK).focus();
    const viewBefore = await screenView();
    await p().keyboard.press('Escape');
    await settle();
    escape = { viewBefore, mapDrawn: await mapDrawn(), viewAfter: await screenView(), focus: await focusNow() };
  }
  return { focused, followed, windowReceived, panRan, escape };
}

async function measure(state: string) {
  const facts = await labelFacts();
  if (!facts.picked) {
    record(state, { facts, note: 'no visible in-view label: nothing to focus in this state' });
    return;
  }
  const open = await keysOnLabel(true);
  // CONTROL: the Map CLOSED (from its toggle, if the Escape above left it open), the same key with focus on a label: the pan listener runs
  if (await mapDrawn()) {
    await p().click(sel2('world-view-map'));
    await p().waitForFunction(() => document.querySelector('[data-testid="world-map"]') === null);
  }
  const factsClosed = await labelFacts();
  const closed = factsClosed.picked ? await keysOnLabel(false) : null;
  record(state, { facts, open, control: { factsClosed, closed } });
  assert.equal(open.focused, 'di-mission-label', `${state}: focus is on the label (setup)`);
  assert.notEqual(open.followed, null, `${state}: a character was followed, so the pan observable can change`);
  assert.ok(closed !== null && closed.focused === 'di-mission-label', `${state}: CONTROL setup: focus on a label with the Map closed`);
  assert.equal(closed.panRan, true, `${state}: POSITIVE CONTROL: with the Map closed, the pan listener runs with focus on a label`);
}

test('HOME, the Map open: keys with focus on an agent Mission label', async () => {
  await boot(1440, 900);
  await openMap();
  await measure('Home, Map open @1440');
}, STEPS_MS);

test('A docked Mission, the panel MINIMIZED, the Map open: keys with focus on an agent Mission label', async () => {
  await boot(1440, 900);
  await dockFirst();
  await p().click(sel2('di-office-panel-minimize'));
  await settle();
  await openMap();
  await measure('minimized, Map open @1440');
}, STEPS_MS);
