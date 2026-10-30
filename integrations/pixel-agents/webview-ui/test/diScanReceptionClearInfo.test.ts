/**
 * R12, first half (AC-4.7-06): Clear info on REAL (office-source) information items, through the actual App in a real browser.
 *
 * Why this file exists. The earlier click-level R12 test (diScanPassB) drives Clear info on the SPECIMEN cast (`source: 'fixture'`), and the `receptionFromInbox` tests check information items only at the MODEL level. Nothing
 * clicked Clear info on cards the real inbox model produces. This does, and it checks the four things the control promises: it clears information items only (action items have no control and do not move); it is
 * view-only (nothing is sent to the office); it is never persisted (the information is back when the page is left and re-opened); and the header and the rail badge, which read the action items, do not change.
 *
 * The office state is the REAL projection of the emitted COMPLETED Mission (`di-office-state-evidence-historical-v2.FIXTURE.json`) with TWO labelled SYNTHETIC delivery-source information entries added to its Reception
 * (`needsYou: false`), the same kind of entry `diScanReceptionPage.test.ts` adds at the model level. Nothing here is live, and this is NOT evidence about a handled-history list (R12's other half), which this does not build or test.
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

import type { DiOfficeState, DiReceptionSourceEntry } from '../../core/src/messages.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<string, DiOfficeState> };
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

const info = (n: number): DiReceptionSourceEntry => ({
  key: `grok:info-${n}`,
  signature: `s${n}`,
  category: 'delivery_decision',
  sourceId: 'grok_delivery_presentations',
  proposalDigest: String(n).repeat(64),
  state: 'authorized',
  needsYou: false,
  presentedAt: 1_800_000_000 + n,
  expiresAt: 1_800_003_600 + n,
  mode: 'pr',
  repositoryUrl: null,
  workflowId: null,
  matchingDeliveries: 1,
  linkState: 'unlinked',
  missionRef: null,
  authorizationCheck: 'checked',
  current: true,
  staleReasons: [],
  route: 'grok',
});
/** The real projection of the finished Mission, plus two SYNTHETIC information entries in its Reception. */
const stateWithInfo = (): DiOfficeState => {
  const state = structuredClone(fixture.states.live!);
  state.reception = { ...state.reception!, sourceEntries: [info(1), info(2)] };
  return state;
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
        } else sentByPage.push(msg);
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
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (reception clear info): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const countOf = (selector: string) => p().locator(selector).count();
const textOf = async (selector: string) => ((await p().locator(selector).first().textContent()) ?? '').trim();

async function boot(state: DiOfficeState): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-open-reception'));
}

async function openReceptionAll(): Promise<void> {
  await p().click(sel('di-open-reception'));
  await p().waitForSelector(sel('di-reception-page'));
  await p().click(sel('di-reception-filter-all'));
  await p().waitForSelector(sel('di-reception-info'));
}

test(
  'CLEAR INFO ON OFFICE-SOURCE INFORMATION ITEMS (browser): it clears the two information items only; action items, the header count and the rail badge do not move; nothing is sent; the items are back when the page is re-opened',
  async () => {
    await boot(stateWithInfo());
    const badge = await textOf(sel('di-rail-reception-count'));
    await openReceptionAll();

    // the page is the OFFICE's (not the specimen): no sample-data tag, and two information cards the real inbox model produced
    // (the sample page says "Sample data (fixture)" in its own text; asserting its absence by text, since the tag carries no test id)
    assert.doesNotMatch(await textOf(sel('di-reception-page')), /Sample data \(fixture\)/, 'the page is the office\'s, not the specimen');
    const infoCards = '[data-testid="di-reception-card"][data-kind="info"]';
    assert.equal(await countOf(infoCards), 2, 'two information items, from the office state');
    const actionBefore = (await countOf(sel('di-reception-card'))) - 2;
    const header = await textOf(sel('di-reception-count'));
    assert.equal(await countOf(sel('di-reception-clear-info')), 1, 'Clear info is offered while information is listed');

    // an action item has no clear or hide control of any kind
    const actionCards = await p().locator(`${sel('di-reception-card')}:not([data-kind="info"])`).allTextContents();
    for (const text of actionCards) assert.doesNotMatch(text, /Clear|Hide|Dismiss/, 'an action item carries no clear control');

    sentByPage = [];
    await p().click(sel('di-reception-clear-info'));
    assert.equal(await countOf(infoCards), 0, 'both information items are cleared from the view');
    assert.equal(await textOf(sel('di-reception-info-none')), 'Information cleared in this view only.');
    assert.equal(await countOf(sel('di-reception-clear-info')), 0, 'nothing left to clear: the control goes');
    assert.equal((await countOf(sel('di-reception-card'))) , actionBefore, 'the action items did not move');
    assert.equal(await textOf(sel('di-reception-count')), header, 'the header count is untouched');
    assert.equal(await textOf(sel('di-rail-reception-count')), badge, 'the rail badge is untouched');

    // VIEW-ONLY: the click sent nothing to the office
    await p().waitForTimeout(400);
    assert.deepEqual(sentByPage, [], `Clear info sent nothing: ${JSON.stringify(sentByPage)}`);

    // NEVER PERSISTED: leave the page and open it again: the information is back
    await p().click(sel('di-rail-home'));
    await p().click(sel('di-open-reception'));
    await p().waitForSelector(sel('di-reception-page'));
    await p().click(sel('di-reception-filter-all'));
    assert.equal(await countOf(infoCards), 2, 'the information is back when the page is re-opened: the clear was a preference of that view only');
    await page!.close();
    page = null;

    // and a fresh load of the same state shows both again (nothing was written anywhere)
    await boot(stateWithInfo());
    await openReceptionAll();
    assert.equal(await countOf(infoCards), 2);
  },
  STEPS_MS,
);

test(
  'WITH NO INFORMATION LISTED (browser, the real projection alone): there is no Clear info control and the page says so',
  async () => {
    await boot(structuredClone(fixture.states.live!));
    await openReceptionAll();
    assert.equal(await countOf('[data-testid="di-reception-card"][data-kind="info"]'), 0);
    assert.equal(await countOf(sel('di-reception-clear-info')), 0, 'no control for nothing');
    assert.equal(await textOf(sel('di-reception-info-none')), 'No information items are listed.');
  },
  STEPS_MS,
);
