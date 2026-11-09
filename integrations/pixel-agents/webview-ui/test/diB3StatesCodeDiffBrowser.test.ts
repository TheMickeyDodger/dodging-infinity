/**
 * B3, 1C G3: THE CODE DIFF ERROR ADVISES "REFRESH" AND THE SCAN FACE HAS NO REFRESH (NOT a canonical test while it is in the evidence tree; PREPARED, NOT RUN). Records with the prefix B3ST. THE RED COMES FIRST: run on the CURRENT
 * bytes before any fix (the Lead: each surviving gap is verified rendered before it is fixed). Source: the retained gap table (a reading), `diCodeReviewModel.ts:962` and `DiScanPanel.tsx:137`.
 *
 * THE INSTRUMENT: the synthetic review feed refuses a file read the way a moved workspace does (`snapshot_moved`, retryable, whose words say "Refresh to see the current changes"), and counts the reads it is asked for.
 *   ADVICE   (STATE) the refused file's notice is drawn and its words advise Refresh.
 *   CONTROL  the face offers a control named Refresh (it had none).
 *   ACTS     reaching it by REAL sequential Tab presses (counted) and pressing Enter asks the overview again (the count rises) and, the feed answering now, the file's changes are drawn; where focus is afterwards is RECORDED, not asserted.
 * HONEST BOUNDS: Chrome, not Brave; the sample read of the review fixture; the refusal code is the one the model's own words advise Refresh for (the only message that says the word).
 * NEGATIVE CONTROL (on the CURRENT bytes): CONTROL fails AT its claim (no Refresh button), after the ADVICE state line passes; ACTS fails AT its first claim for the same reason.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
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

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3ST_SHOTS ?? null;


let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** B3 1C G3: refuse the file reads the way a moved workspace does, and count the reads asked for. */
let refuseFiles = false;
let overviewReads = 0;
let fileReads = 0;
let refuseOverview = false;
let holdOverview = false;
let heldOverview: (() => void) | null = null;

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  return stored === undefined ? null : structuredClone(stored);
}

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
        } else if (msg.type === 'diCodeReviewRead' || msg.type === 'diCodeReviewMissionRead') {
          const found = replyFor(msg);
          if (found === null) return;
          if (msg.fileId === undefined) overviewReads += 1;
          else fileReads += 1;
          const reply =
            refuseFiles && msg.fileId !== undefined
              ? { ...found, snapshot: String(msg.snapshot), state: 'refused', decidedBy: 'backend', problem: 'snapshot_moved', detail: 'moved', target: null, comparison: null, file: null, overview: null }
              : found;
          const answer =
            msg.fileId === undefined && refuseOverview
              ? { ...reply, state: 'refused', decidedBy: 'backend', problem: 'synthetic_unreadable', detail: 'SYNTHETIC: the review could not be read', target: null, comparison: null, file: null, overview: null }
              : reply;
          const payload = JSON.stringify(typeof msg.requestId === 'string' ? { ...answer, requestId: msg.requestId } : answer);
          if (msg.fileId === undefined && holdOverview) {
            heldOverview = () => ws.send(payload);
            return;
          }
          ws.send(payload);
        }
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
    console.warn(`SKIPPED (B3 states): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  refuseFiles = false;
  overviewReads = 0;
  fileReads = 0;
  refuseOverview = false;
  holdOverview = false;
  heldOverview = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B3ST ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B3ST SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(doc.states.live));
  await p().waitForSelector('[data-testid="di-rail-orchestrator"]');
}

async function openCodeDiff(): Promise<void> {
  await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
  await p().waitForSelector('[data-testid="di-scan-tree-file"]');
}



// ── B3 1C G3: the refused file read and its Refresh ─────────────────────────────────────────────────────────────────────────────────────────────────
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const sel = (id: string) => `[data-testid="${id}"]`;
const focusKey = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
const refreshButtons = () => p().getByRole('button', { name: 'Refresh', exact: true });
/** Open the Code Diff, make the feed refuse file reads, and choose a file: the refused notice is drawn and advises Refresh. */
async function atRefusedFile(vp: { width: number; height: number }): Promise<void> {
  await boot(vp);
  await openCodeDiff();
  refuseFiles = true;
  const filesBefore = fileReads;
  await p().locator(sel('di-scan-tree-file'), { hasText: 'syntax_sample.ts' }).first().click();
  await p().waitForSelector(`${sel('di-scan-file-notice')}[data-state="refused"]`, { timeout: 15_000 });
  assert.ok(fileReads > filesBefore, 'STATE: the feed was asked for the file and refused it');
  const notice = (await p().locator(sel('di-scan-file-notice')).textContent()) ?? '';
  assert.ok(/Refresh/.test(notice), `STATE: the refused file's notice advises Refresh ("${notice.trim().slice(0, 120)}")`);
}

for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 STATES G3, Code Diff, at ${where}, CONTROL: an error that advises Refresh offers a Refresh`, async () => {
    await atRefusedFile(vp);
    const n = await refreshButtons().count();
    record(`CONTROL ${vp.width}x${vp.height}`, { refreshButtons: n });
    await shot(`b3st-refresh-advice-${vp.width}x${vp.height}.png`);
    assert.equal(n, 1, `CONTRACT (CONTROL): ${where}: the notice advises Refresh and the face has ${n} Refresh controls`);
  });
  test(`B3 STATES G3, Code Diff, at ${where}, ACTS: Refresh, by keyboard, asks again and the file's changes are drawn`, async () => {
    await atRefusedFile(vp);
    const refresh = refreshButtons();
    assert.equal(await refresh.count(), 1, `CONTRACT (ACTS): ${where}: there is no Refresh to use`);
    refuseFiles = false;
    const before = overviewReads;
    // REAL sequential keys, from where the click left focus (the tree row), counted: a programmatic focus() is NOT keyboard reachability (red 2's ACTS used focus(): b3-1c/red-2.BOUND-focus-is-not-reachability.txt)
    let presses = 0;
    for (; presses < 150 && (await focusKey()) !== 'di-scan-file-refresh'; presses += 1) await p().keyboard.press('Tab');
    record(`ROUTE ${vp.width}x${vp.height}`, { presses, focus: await focusKey() });
    assert.equal(await focusKey(), 'di-scan-file-refresh', `CONTRACT (ROUTE): ${where}: Refresh is reached by sequential Tab presses (${presses} presses, then it was not)`);
    await p().keyboard.press('Enter');
    for (let i = 0; i < 50 && overviewReads === before; i++) await p().waitForTimeout(100);
    assert.ok(overviewReads > before, 'CONTRACT (ACTS): using Refresh asked for the overview again');
    await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
    // THE FOCUS DESTINATION, measured AFTER the state settles (never from one immediate read): the control removes itself on success, so it owes a surviving, meaningful landing BY IDENTITY: the editor bar's path label, the refreshed content's own name (the scroller itself is focusable only while it overflows, and tabIndex -1 on it removes it from the Tab order B2d pins)
    await p().waitForTimeout(1500);
    const first = await focusKey();
    await p().waitForTimeout(1500);
    const second = await focusKey();
    record(`ACTS ${vp.width}x${vp.height}`, { overviewReads: overviewReads - before, settledFocus: [first, second] });
    assert.deepEqual([first, second], ['di-scan-editor-path', 'di-scan-editor-path'], `CONTRACT (FOCUS): ${where}: after a successful Refresh and a settle, focus is ${first} then ${second}, not the path label that names the refreshed changes`);
  });

  if (vp.width >= 1440)
    test(`B3 STATES G3, Code Diff, at ${where}, INTENT: Refresh on a refused overview, focus moved ON PURPOSE to the Home search, then the held read arrives: the landing does not steal it`, async () => {
      refuseOverview = true;
      await boot(vp);
      await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
      await p().click('[data-testid="di-scan-code-diff"]');
      await p().waitForSelector(sel('di-scan-diff-refused'), { timeout: 15_000 });
      assert.equal(await refreshButtons().count(), 1, 'STATE: the refused overview offers Refresh');
      refuseOverview = false;
      holdOverview = true;
      await refreshButtons().click();
      for (let i = 0; i < 40 && heldOverview === null; i++) await p().waitForTimeout(100);
      assert.ok(heldOverview !== null, 'STATE: the next read is HELD');
      await p().locator(sel('di-scan-search')).click();
      assert.equal(await focusKey(), 'di-scan-search', 'STATE: focus was moved on purpose to the Home search');
      holdOverview = false;
      heldOverview?.();
      await p().waitForSelector(sel('di-scan-tree-file'), { timeout: 15_000 });
      await p().waitForTimeout(2500);
      record(`INTENT ${vp.width}x${vp.height}`, { focusSettled: await focusKey() });
      assert.equal(await focusKey(), 'di-scan-search', `CONTRACT (INTENT): ${where}: the late read moved focus away from where the person put it`);
    });
}
