/**
 * G1/G2 through the actual App in a real browser: "Continue in the request workspace" on New Mission hands the typed ask to the EXISTING workspace's Ask draft (its one driver) and opens it there.
 *
 * THE TESTED REQUIREMENT IS THAT THE TEXT ARRIVES, IN FULL (not that navigation occurred). Also: an identical draft is a no-op success; a different unsent draft REFUSES the handoff VISIBLY and
 * touches neither text; Cmd Return does the same as the control; and nothing is sent to the request panel by any of it (no turn, no submit: the human gate stays in the workspace).
 *
 * The office state is the session-mismatch v2 fixture (real projection output). The request panel's answer is ONE labelled SYNTHETIC overview reply (no requests), sent by this test in the reply shape
 * the page parses, so the workspace is available; every other request-panel call is recorded and left unanswered. Nothing is live.
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

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }
).state;
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
/** SYNTHETIC: the request panel's overview, with no requests, so the workspace is available. */
const OVERVIEW = { server_now: 1_800_000_100, capabilities: { decision: {} }, requests: [], requests_truncated: false, conversations: [] };

/** A text that loses something visible if any part of the hop trims, collapses, normalises or truncates it. */
const TEXT = '  Fix the login timeout — keep sessions alive.\n\nSecond paragraph: ünïcödé ✓, two  spaces, a tab\there.  ';
const OTHER = 'Add a dark mode toggle';

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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh')
          ws.send(JSON.stringify({ type: 'diMissionRequestReply', seq: msg.seq, op: 'refresh', state: 'ok', problem: null, reason: null, answer: OVERVIEW }));
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
    console.warn(`SKIPPED (ask handoff): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const panelOps = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op));
const SENDING_OPS = ['turn', 'submit', 'decide', 'withdraw', 'run'];

async function bootToNewMission(): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  // the workspace becomes available once the overview is read: the existing link is enabled
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
}

const composer = () => p().locator(sel('di-mr-composer'));
const askOnPage = () => p().locator(sel('di-new-ask'));
/**
 * Closes the workspace by ACTIVATING its own Close control (a DOM click), not by a pointer click: at 1440 x 900 a pointer click on "Close requests" is intercepted by the scan panel's New Mission page,
 * whichever way the workspace was opened (also from the existing, unchanged workspace link). That occlusion is a separate, recorded finding; this file tests the ask's arrival, not the workspace's place.
 */
async function closeWorkspace(): Promise<void> {
  await p().$eval(sel('di-mr-close'), (el) => (el as HTMLElement).click());
  await p().waitForSelector(sel('di-mr-composer'), { state: 'detached' });
}

test(
  '(a) THE TEXT ARRIVES IN FULL, and (c) pressing again with identical text changes nothing: the workspace opens on Ask holding exactly the typed text; nothing is sent; the page keeps its text',
  async () => {
    await bootToNewMission();
    await askOnPage().fill(TEXT);
    const control = p().locator(sel('di-new-continue'));
    assert.equal(((await control.textContent()) ?? '').trim(), 'Continue in the request workspace');
    await control.click();
    await composer().waitFor();
    assert.equal(await p().locator(sel('di-mr-tab-ask')).getAttribute('aria-selected'), 'true');
    assert.equal(await composer().inputValue(), TEXT, 'the full text arrived, byte for byte');
    assert.equal(await askOnPage().inputValue(), TEXT, 'the page keeps its text');
    assert.deepEqual(panelOps().filter((op) => SENDING_OPS.includes(op)), [], `nothing was sent: ${JSON.stringify(panelOps())}`);

    // (c) identical: a no-op success, no refusal
    await closeWorkspace();
    await control.click();
    await composer().waitFor();
    assert.equal(await composer().inputValue(), TEXT);
    assert.equal(await p().locator(sel('di-new-continue-refused')).count(), 0, 'identical text is not a conflict');
    assert.deepEqual(panelOps().filter((op) => SENDING_OPS.includes(op)), []);
  },
  STEPS_MS,
);

test(
  '(b) A DIFFERENT UNSENT DRAFT: the handoff is refused VISIBLY; the workspace draft and the page text are both untouched; the person can open the workspace to look',
  async () => {
    await bootToNewMission();
    // an unsent draft already in the workspace's Ask, typed there by the person
    await p().click(sel('di-new-open-workspace'));
    await composer().waitFor();
    await composer().fill(OTHER);
    await closeWorkspace();

    await askOnPage().fill(TEXT);
    await p().click(sel('di-new-continue'));
    const refused = p().locator(sel('di-new-continue-refused'));
    await refused.waitFor();
    // the refusal appears inside a live region that was already on the page, so it is announced
    assert.equal(await p().locator(`[role="status"] ${sel('di-new-continue-refused')}`).count(), 1, 'the refusal is in a status region');
    const said = (await refused.textContent()) ?? '';
    assert.match(said, /already holds an unsent draft/);
    assert.match(said, /Nothing was changed/);
    assert.match(said, /Ask/);
    assert.equal(await composer().count(), 0, 'the workspace was not opened over the person\'s choice');
    assert.equal(await askOnPage().inputValue(), TEXT, 'the page text is untouched');

    // where to look: the existing link opens the workspace, whose draft is untouched
    await p().click(sel('di-new-open-workspace'));
    await composer().waitFor();
    assert.equal(await composer().inputValue(), OTHER, 'the workspace draft is untouched');
    assert.deepEqual(panelOps().filter((op) => SENDING_OPS.includes(op)), [], `nothing was sent: ${JSON.stringify(panelOps())}`);
  },
  STEPS_MS,
);

/** Puts a draft into the workspace's Ask the way a person does (open it from the existing link, type, close it). */
async function setWorkspaceDraft(text: string): Promise<void> {
  await p().click(sel('di-new-open-workspace'));
  await composer().waitFor();
  await composer().fill(text);
  await closeWorkspace();
}
const notice = () => p().locator(sel('di-new-continue-refused'));

test(
  'ROUND 35, STALENESS: the refusal notice is withdrawn the moment its condition stops holding (the draft cleared, or made identical), without another attempt',
  async () => {
    await bootToNewMission();
    await setWorkspaceDraft(OTHER);
    await askOnPage().fill(TEXT);
    await p().click(sel('di-new-continue'));
    await notice().waitFor();

    // the person follows the notice, CLEARS that draft, and comes back: nothing is held any more, so nothing may say it is
    await setWorkspaceDraft('');
    assert.equal(await notice().count(), 0, 'the notice is gone once the workspace holds no draft');

    // refused again, then the draft is made IDENTICAL to the page text: no conflict remains, so no notice either
    await setWorkspaceDraft(OTHER);
    await p().click(sel('di-new-continue'));
    await notice().waitFor();
    await setWorkspaceDraft(TEXT);
    assert.equal(await notice().count(), 0, 'the notice is gone once the draft is identical');
    assert.equal(await askOnPage().inputValue(), TEXT, 'the page text is untouched throughout');
  },
  STEPS_MS,
);

test(
  'ROUND 35, THE TRAP: a conflicting draft with NO Continue attempt shows no refusal (nothing was refused), and the status region stays silent',
  async () => {
    await bootToNewMission();
    await setWorkspaceDraft(OTHER);
    await askOnPage().fill(TEXT);
    await p().waitForTimeout(300);
    assert.equal(await notice().count(), 0, 'no attempt, no refusal');
    assert.equal(((await p().locator(sel('di-new-continue-status')).textContent()) ?? '').trim(), '', 'the live region says nothing');
  },
  STEPS_MS,
);

test(
  'CMD RETURN in the ask does what the control does: the full text arrives in the workspace Ask',
  async () => {
    await bootToNewMission();
    await askOnPage().fill(TEXT);
    await askOnPage().focus();
    await p().keyboard.press('Meta+Enter');
    await composer().waitFor();
    assert.equal(await composer().inputValue(), TEXT);
    assert.equal(await askOnPage().inputValue(), TEXT, 'the key added no line break to the page text');
  },
  STEPS_MS,
);
