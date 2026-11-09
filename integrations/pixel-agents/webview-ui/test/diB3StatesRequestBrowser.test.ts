/**
 * B3, 1C G1: A REQUEST READ THAT FAILED MUST NOT BE DRAWN AS "READING" (NOT a canonical test while it is in the evidence tree; PREPARED, NOT RUN). Records with the prefix B3ST. THE RED COMES FIRST: this input is written to be run on the
 * CURRENT bytes before any fix (the Lead: each surviving gap is verified rendered before it is fixed). Source: the retained gap table (a reading) at diMissionRequestModel.ts:1681 to DiMissionRequest.tsx:1487.
 *
 * THE INSTRUMENT: the synthetic panel's overview carries one request (SYNTHETIC_PENDING_REF); its read of that request is REFUSED (or HELD, or answered) on command, and every read it is asked for is counted.
 *   UNREAD  after the first read of the picked request is refused, with NO read outstanding, the pane does not say "Reading this request..." (it says it could not be read).
 *   AGAIN   the pane offers "Read it again"; using it re-asks (the count rises) and, while that read is HELD, the pane reads Reading (truthfully) with focus on the status wrapper, never the page.
 * STATE preconditions come first and are loud: the request row is drawn, the picked request's read was refused (the server-side count), and no read is outstanding.
 * HONEST BOUNDS: Chrome, not Brave; synthetic panel; the wording is the candidate's NEW copy; where focus goes after a SUCCESSFUL second read (the wrapper is replaced by the request's detail) is RECORDED, not asserted.
 * NEGATIVE CONTROL (on the CURRENT bytes): UNREAD fails AT its claim (the pane still says Reading); AGAIN fails AT its first claim (no "Read it again"), both after their STATE lines pass.
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

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  relayReply,
  SYNTHETIC_PENDING_REF,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverviewWithRequests,
  syntheticRequest,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const REVIEW = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3ST_SHOTS ?? null;
/** The panel's own cap on first words (mission_panel/service.py:70). */
const FIRST_WORDS_CHARS = 120;
const LONG: Array<{ id: string; ask: string }> = [16, 28, 40, 52, 64, 76, 88, 96, 104, 112, 118, 120].map((n, i) => {
  const base = `SYNTHETIC ask ${String(i + 1).padStart(2, '0')}: make the readiness probe stable under load and report what changed in the subsystem`;
  const cut = base.padEnd(FIRST_WORDS_CHARS, ' and its tests').slice(0, n);
  return { id: `cv-${'0123456789ef'[i].repeat(32)}`, ask: cut.endsWith(' ') ? `${cut.slice(0, -1)}s` : cut };
});

/** Each conversation's turns as the synthetic panel holds them: a read returns them all, a turn sent joins them. */
let turnsOf: Record<string, Array<ReturnType<typeof syntheticTurn>>> = {};
/** Every request the page sent, in order (to show that a deselection reads nothing again and sends nothing). */
let sent: Array<{ op: string; conversationId: string | null }> = [];
/** B2e correction: the conversations whose reads the synthetic panel REFUSES (the states test's own refusal reply). */
let refuse = new Set<string>();
/** B3 1C G1: how the synthetic panel answers a read of the picked request. */
let requestMode: 'answer' | 'refuse' | 'hold' = 'answer';
let requestReads = 0;
let requestRefusals = 0;
/** The conversation a fresh send (from the blank page) starts. */
const FRESH = `cv-${'d'.repeat(32)}`;
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
        if (msg.type === 'diCodeReviewRead') {
          // the fake office's reply, as diCodeReview.test.ts relays it: the overview for a read with no fileId, else the file's reply, echoing the requestId
          const stored = msg.fileId === undefined ? REVIEW.replies.overview : Object.values(REVIEW.replies.files).find((r) => r.fileId === msg.fileId);
          if (stored !== undefined) ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...stored, requestId: msg.requestId } : stored));
          return;
        }
        if (msg.type !== 'diMissionRequest') return;
        sent.push({ op: String(msg.op), conversationId: msg.conversationId === undefined || msg.conversationId === null ? null : String(msg.conversationId) });
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverviewWithRequests([SYNTHETIC_PENDING_REF], LONG.map((c) => syntheticConversationSummary(c.id, c.ask)))));
        if (msg.op === 'request') {
          requestReads += 1;
          if (requestMode === 'refuse') {
            requestRefusals += 1;
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'request', state: 'refused', problem: 'mission_panel_request_unreadable', reason: 'SYNTHETIC: that request could not be read', answer: null });
          } else if (requestMode === 'answer') send(relayReply(msg.seq, 'request', syntheticRequest(String(msg.requestRef))));
        }
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          if (refuse.has(id)) {
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read.', answer: null });
            return;
          }
          if (turnsOf[id] === undefined) turnsOf[id] = [syntheticDraftedTurn(LONG.find((c) => c.id === id)?.ask ?? 'SYNTHETIC')];
          send(relayReply(msg.seq, 'conversation', syntheticConversation(turnsOf[id], id)));
        }
        if (msg.op === 'turn') {
          const into = msg.conversationId === undefined || msg.conversationId === null ? FRESH : String(msg.conversationId);
          const turn = syntheticTurn(String(msg.text), { conversation_id: into, turn_id: `ct-${'7'.repeat(32)}`, sequence: (turnsOf[into]?.length ?? 0) + 1 });
          turnsOf[into] = [...(turnsOf[into] ?? []), turn];
          send(relayReply(msg.seq, 'turn', { ok: true, turn }));
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
    console.warn(`SKIPPED (B3 states): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  turnsOf = {};
  sent = [];
  refuse = new Set();
  requestMode = 'answer';
  requestReads = 0;
  requestRefusals = 0;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const chipFor = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B3ST ${name}: ${JSON.stringify(r)}`);

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
  for (const c of LONG) await chipFor(c.id).waitFor({ timeout: 20_000 });
}


// ── B3 1C G1: the failed Request read ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const ASK = 'SYNTHETIC an ask typed before the request workspace opens';
const ROW = sel(`di-mr-request-${SYNTHETIC_PENDING_REF}`);
const focusKey = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName.toLowerCase());
  });
/** Open the request workspace on its Requests, pick the one request, and wait until its first read was refused and none is outstanding. */
async function pickRefused(vp: { width: number; height: number }): Promise<void> {
  requestMode = 'refuse';
  await boot(vp);
  await p().locator(sel('di-new-ask')).fill(ASK);
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
  await p().click(sel('di-mr-tab-requests'));
  await p().waitForSelector(ROW, { timeout: 15_000 });
  const before = requestRefusals;
  await p().click(ROW);
  await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-reading-request"], [data-testid="di-mr-request-unread"]') !== null, undefined, { timeout: 10_000 });
  for (let i = 0; i < 40 && requestRefusals === before; i++) await p().waitForTimeout(100);
  assert.ok(requestRefusals > before, 'STATE: the panel refused the first read of the picked request');
  await p().waitForTimeout(400);
}

for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 STATES G1, Request workspace, at ${where}, UNREAD: a refused read of the picked request is not drawn as Reading`, async () => {
    await pickRefused(vp);
    const shown = await p().evaluate(() => ({
      reading: document.querySelector('[data-testid="di-mr-reading-request"]') !== null,
      unread: document.querySelector('[data-testid="di-mr-request-unread"]') !== null,
      text: (document.querySelector('[data-testid="di-mr-reading-request"], [data-testid="di-mr-request-unread"]')?.textContent ?? '').trim(),
    }));
    record(`UNREAD ${vp.width}x${vp.height}`, { ...shown, refusals: requestRefusals, reads: requestReads });
    await shot(`b3st-unread-${vp.width}x${vp.height}.png`);
    assert.equal(shown.reading, false, `CONTRACT (UNREAD): ${where}: the pane says "${shown.text}" after the read was refused and none is outstanding`);
    assert.equal(shown.unread, true, `CONTRACT (UNREAD): ${where}: the pane does not say the request could not be read`);
  });
  test(`B3 STATES G1, Request workspace, at ${where}, AGAIN: the control re-asks, and the pending read is truthful with focus kept`, async () => {
    await pickRefused(vp);
    const again = p().locator(sel('di-mr-read-again'));
    assert.equal(await again.count(), 1, `CONTRACT (AGAIN): ${where}: the pane offers no way to read the request again`);
    const reads = requestReads;
    requestMode = 'hold';
    // REAL sequential keys from the picked row, counted (a programmatic focus() is not keyboard reachability)
    let presses = 0;
    for (; presses < 80 && (await focusKey()) !== 'di-mr-read-again'; presses += 1) await p().keyboard.press('Tab');
    assert.equal(await focusKey(), 'di-mr-read-again', `CONTRACT (ROUTE): ${where}: Read it again is reached by sequential Tab presses (${presses} presses, then it was not)`);
    record(`ROUTE ${vp.width}x${vp.height}`, { presses });
    await p().keyboard.press('Enter');
    for (let i = 0; i < 40 && requestReads === reads; i++) await p().waitForTimeout(100);
    record(`AGAIN ${vp.width}x${vp.height}`, { readsBefore: reads, readsAfter: requestReads, focus: await focusKey() });
    assert.ok(requestReads > reads, 'CONTRACT (AGAIN): using the control asked the panel again');
    assert.equal(await p().locator(sel('di-mr-reading-request')).count(), 1, 'CONTRACT (AGAIN): while that read is held, the pane says Reading (truthfully)');
    assert.equal(await focusKey(), 'di-mr-request-state', 'CONTRACT (AGAIN): focus is on the status wrapper, not the page');
  });
  test(`B3 STATES G1, Request workspace, at ${where}, SETTLED: a control that succeeds by ceasing to exist leaves focus on a surviving landing, by identity`, async () => {
    await pickRefused(vp);
    requestMode = 'answer';
    let presses = 0;
    for (; presses < 80 && (await focusKey()) !== 'di-mr-read-again'; presses += 1) await p().keyboard.press('Tab');
    assert.equal(await focusKey(), 'di-mr-read-again', `STATE: ${where}: Read it again is reached by sequential Tab presses (${presses})`);
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-mr-detail'), { timeout: 10_000 });
    await p().waitForTimeout(1500);
    const first = await focusKey();
    await p().waitForTimeout(1500);
    const second = await focusKey();
    record(`SETTLED ${vp.width}x${vp.height}`, { settledFocus: [first, second] });
    assert.deepEqual([first, second], ['di-mr-heading', 'di-mr-heading'], `CONTRACT (FOCUS): ${where}: after the second read succeeded and settled, focus is ${first} then ${second}, not the workspace's labelled heading`);
  });
}

// ── F3b: the request workspace's own Dismiss on a notice (executor1, 2026-11-08) ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 DISMISS, Request workspace notice, at ${where}: Dismiss BY KEYBOARD hands focus to a SEEN destination inside the workspace that is not the control that removed itself`, async () => {
    await pickRefused(vp);
    const dismiss = p().locator(sel('di-mr-dismiss'));
    // ENTRY STATE, proven first: the refused read left a notice with its Dismiss, and the Dismiss is drawn
    const before = await dismiss.count();
    assert.ok(before >= 1, `ENTRY: the refused read left a notice with its Dismiss (${before} Dismiss controls)`);
    assert.equal(await dismiss.first().isVisible(), true, 'ENTRY: the notice\'s Dismiss is drawn');
    await dismiss.first().focus();
    const startedOn = await focusKey();
    await p().keyboard.press('Enter');
    for (let i = 0; i < 30 && (await dismiss.count()) >= before; i += 1) await p().waitForTimeout(100);
    const after = await dismiss.count();
    const landed = await focusKey();
    const focusedLoc = p().locator(':focus');
    const insideWorkspace = (await p().locator('.di-mr :focus').count()) > 0;
    let seenOk = false;
    if (landed !== 'BODY' && (await focusedLoc.count()) > 0) {
      const target = focusedLoc.first();
      const box = await target.boundingBox();
      const view = p().viewportSize();
      if ((await target.isVisible()) && box !== null && view !== null && box.width > 0 && box.height > 0 && box.y >= 0 && box.y + box.height <= view.height && box.x >= 0 && box.x + box.width <= view.width) {
        try {
          await target.click({ trial: true, timeout: 1500 });
          seenOk = true;
        } catch {
          seenOk = false;
        }
      }
    }
    const problems: string[] = [];
    if (startedOn !== 'di-mr-dismiss') problems.push(`STATE: the keyboard started on ${startedOn}, not on Dismiss`);
    if (after >= before) problems.push(`STATE: activating Dismiss did not remove the notice (${before} -> ${after})`);
    if (landed === 'BODY') problems.push('focus fell to the PAGE (BODY): the control removed itself with no surviving destination');
    if (landed === 'di-mr-dismiss') problems.push('focus is on the Dismiss that was just activated');
    if (landed !== 'BODY' && !insideWorkspace) problems.push(`focus landed on ${landed}, outside the request workspace`);
    if (!seenOk) problems.push(`the landing (${landed}) is not SEEN (not in the window, or not hit-testable at its centre)`);
    record(`DISMISS ${vp.width}x${vp.height}`, { before, after, startedOn, landed, insideWorkspace, seenOk, problems });
    assert.deepEqual(problems, [], `CONTRACT (DISMISS LANDING): ${where}: the notice goes, and focus lands on a SEEN destination inside the workspace that is not the control that removed itself (it landed on ${landed})`);
  });
}
