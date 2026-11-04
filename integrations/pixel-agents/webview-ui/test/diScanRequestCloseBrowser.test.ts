/**
 * "Close requests" BY POINTER on the scan face at 1440 x 900, after EITHER entrance from New Mission: the drafted proposal's "Open it in the request workspace to submit" and "Review requests".
 * Through the actual App in a real browser.
 * CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…, B1): the entrances were the handoff ("Continue in the request workspace") and "Open the existing request workspace";
 * New Mission now IS the Ask (one composer, Send drafts in place), so its routes into the workspace are the draft's onward step and "Review requests". The pointer check is unchanged.
 *
 * THE CHECK IS HIT-TESTING, NOT ACTIVATION. The test asks the page what element is at the centre of the real "Close requests" button, clicks THERE with the mouse (a real pointer event that lands on
 * whatever is on top), and asserts that the workspace CLOSED. It never calls the handler and never activates the element directly: activation is what passed while a person could not click it.
 *
 * Closing keeps what it kept before: the ONE Ask draft (New Mission's box and the workspace's Ask are the same text) survives it, and nothing is sent beyond the one drafting turn the person sent
 * (no submit, decide, withdraw or run). The workspace's own keyboard close (Escape) still works.
 *
 * The office state is the session-mismatch v2 fixture (real projection output). The request panel's answers are LABELLED SYNTHETIC (test/diNewMissionSynthetic.ts, in the panel's own wire shape): an
 * overview with no requests, so the workspace is available; and, only after a Send, the turn and a conversation holding its draft. Every other request-panel call is recorded and left unanswered.
 * Nothing is live.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an explicit message.
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
import { availableCapacityOffice, capacityUseOf,FIXTURE_USE } from './diAvailableCapacity.ts';
import { AUTHORITY_OPS, relayReply, syntheticConversation, syntheticDraftedTurn, syntheticOverview, syntheticTurn } from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const VIEWPORT = { width: 1440, height: 900 };
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }
).state;
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
/** SYNTHETIC: the request panel's overview, with no requests, so the workspace is available. */
const OVERVIEW = syntheticOverview();
const TEXT = 'Fix the login timeout — keep sessions alive.\nSecond line: ünïcödé ✓';
const OTHER = 'Add a dark mode toggle';
const SENDING_OPS = ['turn', ...AUTHORITY_OPS];
/** The text of the ONE turn the page sent (null until a Send): the synthetic conversation answers with it, drafted. */
let sentTurn: string | null = null;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let sentByPage: Array<Record<string, unknown>> = [];

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
          resolveConnected(ws);
          return;
        }
        sentByPage.push(msg);
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', OVERVIEW)));
        // SYNTHETIC, after a Send only: the turn is reserved, and its conversation then holds the completed turn with its draft
        if (msg.type === 'diMissionRequest' && msg.op === 'turn') {
          sentTurn = String(msg.text);
          ws.send(JSON.stringify(relayReply(msg.seq, 'turn', { ok: true, turn: syntheticTurn(sentTurn) })));
        }
        if (msg.type === 'diMissionRequest' && msg.op === 'conversation' && sentTurn !== null)
          ws.send(JSON.stringify(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(sentTurn)]))));
      }),
    );
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app-scan.html`;
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
    console.warn(`SKIPPED (request close): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  sentTurn = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const sent = () => sentByPage.filter((m) => m.type === 'diMissionRequest' && SENDING_OPS.includes(String(m.op))).map((m) => String(m.op));
const composer = () => p().locator(sel('di-mr-composer'));
const askOnPage = () => p().locator(sel('di-new-ask'));

/** CHANGED IN Deliverable 2, round 10: the interaction tests run on an explicit AVAILABLE-CAPACITY variation (diAvailableCapacity.ts), never on ambient
 * capacity; the unmodified fixture is the capacity-refusal countercase's own subject. */
const available = availableCapacityOffice(office);
async function bootToNewMission(state: DiOfficeState = available): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: VIEWPORT });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
}

/**
 * THE POINTER CLOSE: bring the real button into view as a person would (scrolling), ask the page what is at its centre, click THERE with the mouse, and require that the workspace closed. If another
 * element is on top, the click lands on it, the workspace stays open, and the test fails naming what was hit.
 */
async function closeByPointer(): Promise<void> {
  const close = p().locator(sel('di-mr-close'));
  await close.scrollIntoViewIfNeeded();
  const box = await close.boundingBox();
  assert.ok(box, '"Close requests" has a box on the page');
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  // what the page has at that point, and (for a failure) the stacking chain of it and of the button: each positioned or stacking ancestor with its z-index
  const hit = await p().evaluate(
    ([px, py]) => {
      const chain = (start: Element | null): string => {
        const out: string[] = [];
        for (let el = start; el !== null && el !== document.documentElement; el = el.parentElement) {
          const cs = getComputedStyle(el);
          if (cs.position !== 'static' || cs.zIndex !== 'auto' || cs.transform !== 'none' || cs.isolation === 'isolate') {
            const name = el.getAttribute('data-testid') ?? el.className.toString().split(' ')[0] ?? el.tagName.toLowerCase();
            out.push(`${name}[${cs.position} z=${cs.zIndex}${cs.transform !== 'none' ? ' transform' : ''}]`);
          }
        }
        return out.join(' < ');
      };
      const el = document.elementFromPoint(px, py);
      if (el === null) return 'nothing';
      const owner = el.closest('[data-testid]');
      const button = document.querySelector('[data-testid="di-mr-close"]');
      return `${el.tagName.toLowerCase()} (inside ${owner?.getAttribute('data-testid') ?? 'no test id'}); its chain: ${chain(el)}; the button's chain: ${chain(button)}`;
    },
    [x, y] as const,
  );
  await p().mouse.click(x, y);
  try {
    await composer().waitFor({ state: 'detached', timeout: 5_000 });
  } catch {
    assert.fail(`a pointer click at the centre of "Close requests" (${Math.round(x)}, ${Math.round(y)}) did not close the workspace; the page has ${hit} there`);
  }
}

const opsSent = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op));
const authoritySent = () => opsSent().filter((op) => (AUTHORITY_OPS as readonly string[]).includes(op));

test(
  'AFTER THE DRAFT\'S ONWARD STEP: "Close requests" closes by POINTER at 1440 x 900; the draft stays in place on New Mission; nothing but the one drafting turn was sent',
  async () => {
    await bootToNewMission();
    await askOnPage().fill(TEXT);
    await p().click(sel('di-new-send'));
    await p().waitForSelector(sel('di-new-draft-open'), { timeout: 20_000 });
    await p().click(sel('di-new-draft-open'));
    await composer().waitFor();
    assert.equal(await p().locator(sel('di-mr-tab-ask')).getAttribute('aria-selected'), 'true', 'the workspace opened on Ask, on the same conversation');
    await p().waitForSelector(sel('di-mr-draft'));

    await closeByPointer();
    assert.equal(await p().locator(sel('di-new-draft')).count(), 1, 'the draft is still in place on New Mission');
    assert.deepEqual(sent(), ['turn'], `only the one drafting turn was sent: ${JSON.stringify(opsSent())}`);
    assert.deepEqual(authoritySent(), [], 'nothing submitted, decided, withdrawn or run');
  },
  STEPS_MS,
);

test(
  'AFTER "Review requests": "Close requests" closes by POINTER at 1440 x 900; the ONE Ask draft survives it, on New Mission and in the workspace alike; nothing is sent',
  async () => {
    await bootToNewMission();
    await askOnPage().fill(TEXT);
    await p().click(sel('di-new-open-workspace'));
    await p().waitForSelector(sel('di-mr-tab-requests'));
    assert.equal(await p().locator(sel('di-mr-tab-requests')).getAttribute('aria-selected'), 'true', 'Review requests opens the workspace on Requests');
    await p().click(sel('di-mr-tab-ask'));
    await composer().waitFor();
    assert.equal(await composer().inputValue(), TEXT, 'one composer: the workspace Ask holds the text typed on New Mission');

    await closeByPointer();
    assert.equal(await askOnPage().inputValue(), TEXT, 'the text survives the close');
    assert.deepEqual(sent(), [], 'nothing was sent');
  },
  STEPS_MS,
);

test(
  'THE KEYBOARD CLOSE IS KEPT: Escape in the workspace closes it, with its draft kept, and that draft IS New Mission\'s box',
  async () => {
    await bootToNewMission();
    await p().click(sel('di-new-open-workspace'));
    await p().click(sel('di-mr-tab-ask'));
    await composer().waitFor();
    await composer().fill(OTHER);
    await composer().focus();
    await p().keyboard.press('Escape');
    await composer().waitFor({ state: 'detached', timeout: 5_000 });
    assert.equal(await askOnPage().inputValue(), OTHER, 'one composer: what was typed in the workspace is in New Mission\'s box');
    assert.deepEqual(sent(), []);
  },
  STEPS_MS,
);

// The capacity REFUSAL this file's interaction tests no longer rest on, pinned in its own body (Deliverable 2, round 10): the unmodified fixture holds 2 and has 14
// that cannot be checked, the guard counts both, and 16 reaches the limit of 15. Send is unavailable and says so; nothing opens and nothing is sent.
test(
  'CAPACITY REFUSED, the unmodified fixture: 2 in use and 14 that cannot be checked reach the limit, so Send is unavailable with that reason; nothing opens or is sent',
  async () => {
    assert.deepEqual(capacityUseOf(office), FIXTURE_USE, 'the fixture as the guard reads it');
    await bootToNewMission(office);
    await askOnPage().fill(TEXT);
    const control = p().locator(sel('di-new-send'));
    assert.equal(await control.getAttribute('aria-disabled'), 'true', 'Send is unavailable at the limit');
    const words = /15 of 15 Orchestrators are in use or cannot be checked \(2 in use, 14 cannot be checked\)\. Finish or stop one to start another\./;
    assert.match((await control.getAttribute('title')) ?? '', words, 'its title gives the reason');
    const described = await control.getAttribute('aria-describedby');
    assert.ok(described, 'the unavailable Send is described by its reason');
    assert.match(((await p().locator(`[id="${described}"]`).textContent()) ?? '').trim(), words, 'its description gives the same reason');
    // a REAL click on the held control (force: Playwright would otherwise wait for it to be enabled): its handler must refuse it
    await control.click({ force: true });
    assert.equal(await composer().count(), 0, 'no workspace opened');
    assert.equal(await askOnPage().inputValue(), TEXT, 'the page keeps its text');
    assert.deepEqual(sent(), [], 'nothing was sent');
  },
  STEPS_MS,
);
