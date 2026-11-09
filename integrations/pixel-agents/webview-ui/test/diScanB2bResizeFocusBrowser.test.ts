/**
 * B2b round 13, the Reviewer's finding 1, RENDERED in Chrome (not Brave evidence): crossing the two-pane breakpoint (1440) changes neither the view nor
 * the docked Mission, yet it can take focus away. Narrowing COVERS Home, so a focused Home row is hidden, and UNMOUNTS the "Go to the Mission list" link,
 * so a focused link is removed. Through the real App (test/harness/di-app-scan.html; the session-mismatch v2 FIXTURE in its labelled available-capacity
 * variation; the request panel SYNTHETIC). Nothing is live.
 *
 * Each claim names the assertion that carries it (method rule 2), and every focus before a resize is reached as a person reaches it (pointer, Shift+Tab,
 * Enter), except where a case says it places focus directly to set up an UNAFFECTED surface:
 *  - LOST BY NARROWING (the defect): with focus on the docked Mission's Home row, or on the list link, narrowing below 1440 hands focus to the docked
 *    screen's Back; the Mission stays docked (the same `data-mission-ref`, Mission details), and Home is covered;
 *  - UNAFFECTED (the fix must not over-reach): focus on a control in the screen (Code Diff) or on the rail stays exactly where it was, across narrowing;
 *    and widening from one pane to two moves no focus.
 * DISCRIMINATING: the two LOST cases fail on the frozen B2b product (focus left hidden or on the page body) and pass with the fix; the UNAFFECTED cases
 * pass on both.
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

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
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
    console.warn(`SKIPPED (B2b resize focus): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;

async function bootApp(width: number, height: number): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}
/** Dock the first Home row with ONE pointer click (B2a); ruling (B) puts focus on the docked screen's Back. Returns the docked Mission's ref. */
async function dockFirst(): Promise<string> {
  await p().click(sel('di-scan-row'));
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().waitForFunction(() => document.activeElement?.getAttribute('data-testid') === 'di-scan-back');
  return (await p().locator(sel('di-scan-stage')).getAttribute('data-mission-ref')) ?? '';
}
/** Resize, wait until the panes say so, then two frames so every effect of the change has run. */
async function resizeTo(width: number, height: number, panes: 'one' | 'two'): Promise<void> {
  await p().setViewportSize({ width, height });
  await p().waitForSelector(`[data-testid="di-scan-panel"][data-panes="${panes}"]`);
  await p().evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
/** Where focus is, and whether the window shows it (a focused control under `visibility: hidden` is not shown). */
const focusNow = () =>
  p().evaluate(() => {
    const a = document.activeElement;
    return {
      id: a === document.body ? 'BODY' : (a?.getAttribute('data-testid') ?? a?.tagName ?? null),
      ref: a?.getAttribute('data-mission-ref') ?? null,
      shown: a !== null && a !== document.body && getComputedStyle(a).visibility === 'visible' && a.getClientRects().length > 0,
    };
  });
const docked = () =>
  p().evaluate(() => {
    const stage = document.querySelector('[data-testid="di-scan-stage"]');
    return { view: stage?.getAttribute('data-view') ?? null, ref: stage?.getAttribute('data-mission-ref') ?? null };
  });

test(
  'B2b r13 LOST BY NARROWING, a focused HOME ROW: at 1440 the docked row is reached as a person does (Shift+Tab to the list link, Enter); narrowed below 1440, focus goes to the docked screen\'s Back and the Mission stays docked',
  async () => {
    await bootApp(1440, 900);
    const ref = await dockFirst();
    await p().keyboard.press('Shift+Tab');
    assert.equal((await focusNow()).id, 'di-scan-to-list', 'Shift+Tab from Back is the list link');
    await p().keyboard.press('Enter');
    const before = await focusNow();
    assert.deepEqual({ id: before.id, ref: before.ref, shown: before.shown }, { id: 'di-scan-row', ref, shown: true }, 'focus on the docked Mission\'s own Home row');
    await resizeTo(1280, 800, 'one');
    assert.equal(await p().locator(sel('di-scan-home')).getAttribute('data-covered'), 'true', 'one pane: Home is covered');
    const after = await focusNow();
    console.log(`r13 HOME ROW, narrowed 1440 -> 1280: focus before ${JSON.stringify(before)}, after ${JSON.stringify(after)}`);
    assert.deepEqual(await docked(), { view: 'mission', ref }, 'the Mission stays docked');
    assert.deepEqual({ id: after.id, shown: after.shown }, { id: 'di-scan-back', shown: true }, 'focus is on the docked screen\'s Back, shown');
  },
  STEPS_MS,
);

test(
  'B2b r13 LOST BY NARROWING, the focused LIST LINK: narrowed below 1440 the link unmounts; focus goes to the docked screen\'s Back and the Mission stays docked',
  async () => {
    await bootApp(1440, 900);
    const ref = await dockFirst();
    await p().keyboard.press('Shift+Tab');
    assert.equal((await focusNow()).id, 'di-scan-to-list', 'focus on the list link');
    await resizeTo(1280, 800, 'one');
    assert.equal(await p().locator(sel('di-scan-to-list')).count(), 0, 'one pane: the link is gone');
    const after = await focusNow();
    console.log(`r13 LIST LINK, narrowed 1440 -> 1280: focus after ${JSON.stringify(after)}`);
    assert.deepEqual(await docked(), { view: 'mission', ref }, 'the Mission stays docked');
    assert.deepEqual({ id: after.id, shown: after.shown }, { id: 'di-scan-back', shown: true }, 'focus is on the docked screen\'s Back, shown');
  },
  STEPS_MS,
);

test(
  'B2b r13 UNAFFECTED: focus in the screen (Code Diff) or on the rail stays exactly where it was across narrowing; widening from one pane to two moves no focus',
  async () => {
    // focus placed DIRECTLY on each unaffected surface (the claim is about the resize, not how it got there)
    await bootApp(1440, 900);
    const ref = await dockFirst();
    await p().locator(sel('di-scan-code-diff')).focus();
    await resizeTo(1280, 800, 'one');
    assert.equal((await focusNow()).id, 'di-scan-code-diff', 'the screen\'s control keeps focus');
    assert.deepEqual(await docked(), { view: 'mission', ref });

    await page?.close();
    await bootApp(1440, 900);
    const ref2 = await dockFirst();
    await p().locator(sel('di-rail-missions')).focus();
    await resizeTo(1280, 800, 'one');
    assert.equal((await focusNow()).id, 'di-rail-missions', 'the rail keeps focus');
    assert.deepEqual(await docked(), { view: 'mission', ref: ref2 });

    await page?.close();
    await bootApp(1280, 800);
    const ref3 = await dockFirst();
    await resizeTo(1600, 1000, 'two');
    assert.equal((await focusNow()).id, 'di-scan-back', 'widening moves no focus');
    assert.equal(await p().locator(sel('di-scan-home')).getAttribute('data-covered'), null, 'two panes: Home is not covered');
    assert.deepEqual(await docked(), { view: 'mission', ref: ref3 });
  },
  STEPS_MS,
);
