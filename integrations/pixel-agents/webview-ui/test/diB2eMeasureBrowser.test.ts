/**
 * B2e, MEASUREMENT ONLY (the Lead: tell me what New Mission actually does before proposing any change). Chrome, headless; not Brave evidence; nothing
 * here changes the product. The real App composition (test/harness/di-app-scan.html) over the session-mismatch v2 fixture in its available-capacity
 * variation, the request panel answered by the LABELLED SYNTHETIC relay of diScanNewMissionDismissBrowser (test/diNewMissionSynthetic.ts). Nothing is live.
 *
 * STATES (asserted, method rule 9): New Mission is open (data-view "new"); Recent is drawn from a READ overview (both synthetic asks present); in PICKED,
 * conversation B is picked and read with turns (its thread drawn, its chip aria-current); in BLANK (the contrast), nothing is picked.
 * MEASURED (recorded "B2EM ...", never asserted): at 1280x800, 1440x900 and 1600x1000, the panes state and whether the Mission list is beside or covered;
 * every New Mission region's box (the page, the thread, the close control, the Ask box, Recent, the picked chip, the request-workspace section); which
 * element scrolls the page and how far; which regions are inside the scroller's VISIBLE area as the page is first drawn (read after the page settles); the
 * vertical distance from the picked chip to its thread; each region's left edge and width (one column or more); the stage's horizontal overflow.
 * Captures (method rule 4: their own directory, never overwritten) are written when DI_B2EM_SHOTS names a directory: the first view, and the view
 * scrolled to Recent.
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
import {
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_OTHER_CONVERSATION_ID,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B2EM_SHOTS ?? null;

const A = SYNTHETIC_CONVERSATION_ID;
const B = SYNTHETIC_OTHER_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A: the conversation that reads';
const ASK_B = 'SYNTHETIC recent ask B: the conversation picked in each state';

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
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
        if (msg.type !== 'diMissionRequest') return;
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview([syntheticConversationSummary(A, ASK_A), syntheticConversationSummary(B, ASK_B)])));
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(id === A ? ASK_A : ASK_B)], id)));
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  url = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}/test/harness/di-app-scan.html`;
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
    console.warn(`SKIPPED (B2e measurement): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const recentAsk = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B2EM ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a capture. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  const file = path.join(SHOTS, name);
  assert.ok(!fs.existsSync(file), `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  // Recent is drawn only from a READ overview: wait for it, so the pick below is a real one
  await recentAsk(A).waitFor({ timeout: 20_000 });
  await recentAsk(B).waitFor({ timeout: 20_000 });
}

/** Everything measured, read AFTER the page has settled (two animation frames and a short wait: method rule 7, read at the right moment). */
const measure = () =>
  p().evaluate(async () => {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))));
    const q = (s: string) => document.querySelector<HTMLElement>(s);
    const pageEl = q('[data-testid="di-new-mission"]')!;
    // the element that scrolls the page: the nearest ancestor whose content overflows and whose overflow-y scrolls
    let scroller: HTMLElement | null = pageEl;
    while (scroller !== null) {
      const cs = getComputedStyle(scroller);
      if (/(auto|scroll)/.test(cs.overflowY) && scroller.scrollHeight > scroller.clientHeight + 1) break;
      scroller = scroller.parentElement;
    }
    const view = scroller ? scroller.getBoundingClientRect() : new DOMRect(0, 0, innerWidth, innerHeight);
    const box = (el: HTMLElement | null) => {
      if (el === null || !el.checkVisibility()) return null;
      const b = el.getBoundingClientRect();
      return {
        left: Math.round(b.left),
        top: Math.round(b.top),
        width: Math.round(b.width),
        height: Math.round(b.height),
        // inside the scroller's visible area as first drawn: wholly, partly or not at all
        visible: b.bottom <= view.top || b.top >= view.bottom ? 'no' : b.top >= view.top && b.bottom <= view.bottom ? 'whole' : 'part',
      };
    };
    const stage = q('[data-testid="di-scan-stage"]');
    const list = q('[data-testid="di-missions"]');
    const regions = {
      page: box(pageEl),
      thread: box(q('[data-testid="di-new-thread"]')),
      closeControl: box(q('[data-testid="di-new-close-conversation"]')),
      askBox: box(q('[data-testid="di-new-ask"]')),
      recent: box(q('[data-testid="di-new-recent"]')),
      pickedChip: box(q('[data-testid="di-new-recent-ask"][aria-current="true"]')),
      requestsSection: box(q('.di-new-requests')),
    };
    return {
      viewport: { width: innerWidth, height: innerHeight },
      panes: q('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
      view: q('[data-testid="di-scan-panel"]')?.getAttribute('data-view') ?? null,
      missionList: list === null ? null : { drawn: list.checkVisibility(), box: box(list) },
      stage: stage === null ? null : { box: box(stage), overflowsX: stage.scrollWidth > stage.clientWidth + 1 },
      scroller: scroller === null ? null : { testid: scroller.getAttribute('data-testid'), className: scroller.className, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight, scrollTop: Math.round(scroller.scrollTop), top: Math.round(view.top), bottom: Math.round(view.bottom) },
      regions,
      pickedChipBelowThreadPx: regions.pickedChip && regions.thread ? regions.pickedChip.top - regions.thread.top : null,
      sectionColumns: [...pageEl.querySelectorAll<HTMLElement>(':scope > section, :scope > div')].filter((el) => el.checkVisibility()).map((el) => ({
        testid: el.getAttribute('data-testid'),
        className: el.className,
        left: Math.round(el.getBoundingClientRect().left),
        width: Math.round(el.getBoundingClientRect().width),
        top: Math.round(el.getBoundingClientRect().top),
      })),
    };
  });

for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  for (const stateName of ['PICKED', 'BLANK'] as const)
    test(`B2e MEASURE at ${vp.width}x${vp.height}, ${stateName}: New Mission with Recent populated${stateName === 'PICKED' ? ' and a conversation picked' : ' and nothing picked (the contrast)'}`, async () => {
      await boot(vp);
      if (stateName === 'PICKED') {
        await recentAsk(B).click();
        await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
        await p().waitForTimeout(400);
      } else {
        await p().waitForTimeout(400);
      }
      const first = await measure();
      await shot(`b2em-${vp.width}x${vp.height}-${stateName.toLowerCase()}-first-view.png`);
      // the view scrolled to Recent, as a person would reach it
      await p().locator(sel('di-new-recent')).scrollIntoViewIfNeeded();
      await p().waitForTimeout(200);
      const atRecent = await measure();
      await shot(`b2em-${vp.width}x${vp.height}-${stateName.toLowerCase()}-at-recent.png`);
      record(`${vp.width}x${vp.height} ${stateName}`, { first, atRecent });
      // STATES only (method rule 9): the outcomes above are the measurement
      assert.equal(first.view, 'new', 'STATE: New Mission is open');
      assert.notEqual(first.regions.recent, null, 'STATE: Recent is drawn');
      if (stateName === 'PICKED') {
        assert.equal(await recentAsk(B).getAttribute('aria-current'), 'true', 'STATE: B is the conversation picked');
        assert.notEqual(first.regions.thread, null, 'STATE: its thread is drawn');
      } else {
        assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, 'STATE: nothing is picked');
      }
    }, STEPS_MS);
