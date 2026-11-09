/**
 * MEASUREMENT ONLY (the Lead, 2026-11-05, after B2c cut 4c): with the Map OPEN beneath an open Mission screen on the scan face, what happens to the
 * keyboard, and does it happen at HEAD (5a1585b) too? Run UNCHANGED on two trees: the live branch (B2a/B2b docking, B2c) and a verified git-archive
 * export of HEAD (every HEAD blob hash-checked). Chrome, the real App (test/harness/di-app-scan.html), the session-mismatch v2 FIXTURE (available
 * capacity), the large world (packaging/di/worlds/living-metropolis.json), the request panel SYNTHETIC. Nothing is live.
 *
 * ASSERTED, only to know the state is the named one: the Map is open and drawn, and a Mission screen is open over it. Everything else is RECORDED,
 * printed into the log ("MAPKEYS ..."), and asserted NOT AT ALL, because this is attribution evidence, not a pin:
 *  - Tab: does focus move inside the open screen (the browser's own action, which the Map's guard does not prevent)?
 *  - the page's own key handlers: do they receive a key (ArrowDown) at all (the guard stops every key at the window, capture phase)?
 *  - Enter on a focused screen control (Code Diff): does it act (a native button's own activation)?
 *  - Escape, twice: what closes each time, the Map (beneath, covered) or the screen (the one shown)?
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
    console.warn(`SKIPPED (map keys measurement): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);
const mapDrawn = async () => (await p().locator(sel('world-map')).count()) === 1;
const screenView = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
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

for (const [w, h] of [
  [1440, 900],
  [1280, 800],
] as const)
  test(`MEASURE at ${w}x${h}: the keyboard while the Map is open beneath an open Mission screen`, async () => {
    const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
    page = await browser!.newPage({ viewport: { width: w, height: h } });
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
    // the world controls are collapsible on the scan face and drawn only while shown: shown first, as a person does
    if ((await p().locator(sel('world-view-map')).count()) === 0) await p().click(sel('di-world-disclosure'));
    await p().click(sel('world-view-map'));
    await p().waitForSelector(sel('world-map'));
    // open a Mission: one click on the branch (B2a docks it), the row then "Open Mission" at HEAD
    await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
    await p().click(sel('di-scan-row'));
    const opened = await p()
      .waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]', { timeout: 1500 })
      .then(() => 'one click')
      .catch(() => null);
    if (opened === null) {
      await p().click(sel('di-scan-open-mission'));
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    }
    // the named state, asserted
    expect(await mapDrawn(), 'the Map is open and drawn').toBe(true);
    expect(await screenView(), 'a Mission screen is open over it').toBe('mission');

    const record: Record<string, unknown> = { opened: opened ?? 'row, then Open Mission' };
    record.focusAtStart = await focusNow();
    await p().locator(sel('di-scan-back')).focus();
    record.focusBeforeTab = await focusNow();
    await p().keyboard.press('Tab');
    record.focusAfterOneTab = await focusNow();
    record.pageHandlersReceiveKeys = await handlersReceiveKeys();
    await p().locator(sel('di-scan-code-diff')).focus();
    await p().keyboard.press('Enter');
    await p().waitForTimeout(300);
    record.enterOnCodeDiff = { view: await screenView(), mapDrawn: await mapDrawn() };
    if ((await screenView()) === 'diff') {
      // back to Mission details by pointer, so the Escape steps start from the same screen
      await p().click(sel('di-scan-back'));
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    }
    await p().keyboard.press('Escape');
    await p().waitForTimeout(300);
    record.firstEscape = { mapDrawn: await mapDrawn(), view: await screenView() };
    await p().keyboard.press('Escape');
    await p().waitForTimeout(300);
    record.secondEscape = { mapDrawn: await mapDrawn(), view: await screenView() };
    console.log(`MAPKEYS @${w}: ${JSON.stringify(record)}`);
  }, STEPS_MS);
