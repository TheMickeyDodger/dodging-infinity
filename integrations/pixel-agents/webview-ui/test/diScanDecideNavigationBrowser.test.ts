/**
 * M9 / R4 through the actual App in a real browser: Mission details' "Decide in the request panel" opens the EXISTING request workspace on that proposal, and sends nothing.
 *
 * The office state is the session-mismatch v2 fixture (its Mission Core record is real projection output). The request panel's answer is ONE labelled SYNTHETIC overview reply, sent by this test in
 * the reply shape the page parses (`parseOverview`): one request of that Mission whose lifecycle offers `decide`. No panel process runs, nothing is live.
 *
 * What it checks, and only that: the navigation is enabled; clicking it opens the workspace on its Requests tab and the page asks for THAT request's record (op `request`, that reference), which is the
 * existing view that draws the decision block for a request offering `decide` (DiMissionRequest.tsx); and the click sends no `decide`. What the decision block then records (the exact approval, or since
 * owner decision (c) a decline, rejection or change request) is the existing workspace's behaviour, proved elsewhere, not here.
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
const core = office.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

const REQUEST_REF = `lr-${'1'.repeat(32)}`;
/** SYNTHETIC: the request panel's overview, one request of this Mission waiting for a decision. */
const OVERVIEW = {
  server_now: 1_800_000_100,
  capabilities: { decision: {} },
  requests: [
    {
      request_ref: REQUEST_REF,
      mission_id: core.nativeId,
      revision: 1,
      created_at: 1_800_000_000,
      objective: 'Synthetic proposal',
      proposal_digest_sha256: 'd'.repeat(64),
      lifecycle: { stage: 'of_record', label: 'Waiting for your decision', terminal: false, actions: ['decide'], notice: null },
    },
  ],
  requests_truncated: false,
  conversations: [],
};

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** Every message the page sent after the handshake, exactly as received. */
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
        // the ONE synthetic answer: the overview; every other request-panel call is recorded and left unanswered
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
    console.warn(`SKIPPED (decide navigation): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const requestOps = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => ({ op: m.op, requestRef: m.requestRef ?? null }));

async function boot(): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(`${sel('di-scan-row')}[data-mission-ref="${core.missionRef}"]`);
}

test(
  'DECIDE IN THE REQUEST PANEL (browser): enabled on the Mission waiting for a decision; the click opens the workspace on that proposal and sends no decision',
  async () => {
    await boot();
    await p().click(`${sel('di-scan-row')}[data-mission-ref="${core.missionRef}"]`);
    await p().click(sel('di-scan-open-mission'));
    const decide = p().locator('[data-testid="di-scan-state-actions"] [data-action="decide"]');
    await decide.waitFor();
    assert.equal(((await decide.textContent()) ?? '').trim(), 'Decide in the request panel');
    // the overview arrives asynchronously: the navigation becomes enabled once the page has read it
    await p().waitForFunction(() => {
      const b = document.querySelector('[data-testid="di-scan-state-actions"] [data-action="decide"]');
      return b instanceof HTMLButtonElement && !b.disabled;
    });
    for (const action of ['request_changes', 'reject'])
      assert.equal(await p().locator(`[data-testid="di-scan-state-actions"] [data-action="${action}"]`).isDisabled(), true, `${action} is drawn disabled`);

    sentByPage = [];
    await decide.click();
    await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-tab-requests"]')?.getAttribute('aria-selected') === 'true');
    // the workspace asks for THAT proposal's record: the view whose decision block is the existing human-gated decision
    const deadline = Date.now() + 10_000;
    while (!requestOps().some((m) => m.op === 'request' && m.requestRef === REQUEST_REF) && Date.now() < deadline) await p().waitForTimeout(100);
    assert.ok(
      requestOps().some((m) => m.op === 'request' && m.requestRef === REQUEST_REF),
      `the workspace read that proposal: ${JSON.stringify(requestOps())}`,
    );
    // a navigation: nothing was decided
    assert.equal(requestOps().filter((m) => m.op === 'decide').length, 0, `no decision was sent: ${JSON.stringify(requestOps())}`);
  },
  STEPS_MS,
);

test(
  'R4 ON THE RECEPTION PAGE (browser): the request-panel proposal card offers Decide in the request panel; the click opens the workspace on that proposal and sends no decision',
  async () => {
    await boot();
    await p().click(sel('di-open-reception'));
    await p().waitForSelector(sel('di-reception-page'));
    const decide = p().locator(sel('di-reception-decide'));
    await decide.waitFor();
    assert.equal(((await decide.textContent()) ?? '').trim(), 'Decide in the request panel');
    assert.equal(await decide.isDisabled(), false);

    sentByPage = [];
    await decide.click();
    await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-tab-requests"]')?.getAttribute('aria-selected') === 'true');
    const deadline = Date.now() + 10_000;
    while (!requestOps().some((m) => m.op === 'request' && m.requestRef === REQUEST_REF) && Date.now() < deadline) await p().waitForTimeout(100);
    assert.ok(
      requestOps().some((m) => m.op === 'request' && m.requestRef === REQUEST_REF),
      `the workspace read that proposal: ${JSON.stringify(requestOps())}`,
    );
    assert.equal(requestOps().filter((m) => m.op === 'decide').length, 0, `no decision was sent: ${JSON.stringify(requestOps())}`);
  },
  STEPS_MS,
);
