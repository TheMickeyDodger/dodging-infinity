/**
 * Deliverable 2, round 9 (the result contract, end to end): in the REAL App, an owner holds "Delete local data" on an archived Mission's own details with the request
 * workspace CLOSED, and the outcome is shown THERE, tied to that Mission. Nothing opens or switches a workspace to make it visible.
 *
 * The office is this test's own socket. It sends the real server projection of an archived Mission Core Mission (di-office-state-progress-abandoned-v2, exported by
 * server/__tests__/diMissionProgressWire.test.ts) and answers the delete with the replies the office's REAL relay emitted for the panel's REAL answers
 * (di-mission-request-delete-replies.FIXTURE.json, from server/__tests__/diMissionPanelDeleteRelay.test.ts, itself fed by tests/test_mission_retention_http.py over the
 * real HTTP route). Only the `seq` is re-addressed to the request the page sent. Nothing is live.
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

import type { DiMissionRequestReply, DiOfficeState } from '../../core/src/messages.ts';
import { MR_DELETE_PENDING_WORDS, MR_DELETE_UNSTATED_WORDS } from '../src/components/diMissionRequestModel.ts';
import { DI_DELETE_HOLD_MS } from '../src/constants.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJson = <T,>(file: string): T => JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', file), 'utf-8')) as T;
const exported = readJson<{ state: DiOfficeState }>('di-office-state-progress-abandoned-v2.FIXTURE.json').state;
const replies = readJson<{ replies: Record<string, DiMissionRequestReply> }>('di-mission-request-delete-replies.FIXTURE.json').replies;
const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
const MISSION_ID = 'mn-00000000000000000000000000000009';
const OTHER_REF = 'synthetic_mission_core:mn-00000000000000000000000000000002';
/** Target identity: the SAME native id in a SECOND registered Mission Core source (a labelled, synthetic variation of the export: the registry permits two
 * mission_core sources, and refuses only a duplicate source id or store). */
const TWIN_REF = 'synthetic_mission_core_two:mn-00000000000000000000000000000009';
const office: DiOfficeState = (() => {
  const state = structuredClone(exported);
  const twin = structuredClone(state.missions.missions.find((m) => m.missionRef === REF)!);
  twin.missionRef = TWIN_REF;
  twin.sourceId = 'synthetic_mission_core_two';
  twin.storeIdentity = 'd'.repeat(64);
  state.missions.missions.push(twin);
  return state;
})();
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** Every delete the page sent, in order. */
let deletes: Array<Record<string, unknown>> = [];
/** The reply each delete is answered with (null: left unanswered, in flight). */
let answerWith: DiMissionRequestReply | null = null;

const answer = (request: Record<string, unknown>, stored: DiMissionRequestReply) =>
  socket!.send(JSON.stringify({ ...stored, seq: request.seq, op: 'deleteLocalData' }));

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
        } else if (msg.type === 'diMissionRequest' && msg.op === 'deleteLocalData') {
          deletes.push(msg);
          if (answerWith !== null) answer(msg, answerWith);
        }
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
    console.warn(`SKIPPED (delete result, Mission details): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  deletes = [];
  answerWith = null;
}, STEPS_MS);

const p = () => page!;
const workspaceOpen = async () => (await p().locator('[data-testid="di-mr"]').count()) > 0;
const result = () => p().locator(`[data-testid="di-scan-delete-result"][data-mission-ref="${REF}"]`);

/** The archived Mission's own details, reached the way a person does (Missions, Archived, the row), with the request workspace CLOSED. */
async function bootAtTheArchivedMission(): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await openMission(REF, true);
  assert.equal(await workspaceOpen(), false, 'the request workspace is closed');
}

async function openMission(ref: string, archived: boolean): Promise<void> {
  await p().waitForSelector('[data-testid="di-rail-missions"]');
  await p().click('[data-testid="di-rail-missions"]', { timeout: 10_000 });
  if (archived) await p().click('[data-testid="di-missions-status-archived"]', { timeout: 10_000 });
  await p().click(`[data-testid="di-missions-row"][data-mission-ref="${ref}"] [data-testid="di-missions-open"]`, { timeout: 10_000 });
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}

/** A real hold on the trash: pointer down, held past the hold time, pointer up. */
async function hold(): Promise<void> {
  await p().locator('[data-testid="di-scan-trash"]').first().hover();
  await p().mouse.down();
  await p().waitForTimeout(DI_DELETE_HOLD_MS + 400);
  await p().mouse.up();
}

async function deleteAnsweredWith(name: string): Promise<string> {
  answerWith = replies[name]!;
  await bootAtTheArchivedMission();
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 1, 'the hold is offered on this archived Mission');
  await hold();
  await result().waitFor({ timeout: 8000 });
  assert.equal(deletes.length, 1, 'exactly one delete was sent');
  assert.deepEqual(
    [deletes[0]!.missionId, deletes[0]!.displayedState, deletes[0]!.missionRef, deletes[0]!.storeIdentity],
    [MISSION_ID, 'AWAITING_DECISION', REF, 'c'.repeat(64)],
    'the source-qualified Mission and the store it is in, never a bare native id',
  );
  assert.equal(await workspaceOpen(), false, 'no workspace was opened to show the result');
  return ((await result().textContent()) ?? '').trim();
}

test('ACCEPTED, PENDING: the Mission details say it started and is finishing, never refused or intact, and offer no second press', async () => {
  const text = await deleteAnsweredWith('pending_started_not_finished');
  assert.equal(await result().getAttribute('data-state'), 'pending');
  assert.equal(text, MR_DELETE_PENDING_WORDS);
  assert.doesNotMatch(text, /Not deleted|Nothing was removed|was deleted/, 'never a refusal, never finished');
  assert.match(text, /It was not refused\./);
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0, 'the hold is withdrawn');
  assert.match((await p().locator('[data-testid="di-scan-trash-reason"]').textContent()) ?? '', /was accepted, so it is not offered again/);
  // correlated: another Mission's details carry no result of it, and this one's still does
  await openMission(OTHER_REF, false);
  assert.equal(await p().locator('[data-testid="di-scan-delete-result"]').count(), 0, 'not shown on another Mission');
  await openMission(REF, true);
  assert.equal(await result().getAttribute('data-state'), 'pending');
}, STEPS_MS);

test('REFUSED, STALE: the refusal and its real reason, nothing removed, and the hold still offered', async () => {
  const text = await deleteAnsweredWith('refused_status_stale');
  assert.equal(await result().getAttribute('data-state'), 'refused');
  assert.match(text, /^Not deleted: the Mission changed since this page showed it\. Nothing was removed\./);
  assert.match(text, /The request panel said: the display showed/, 'the panel’s own reason');
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 1);
}, STEPS_MS);

for (const [name, words] of [
  ['refused_busy', /^Not deleted: a progression pass is acting on it right now; try again shortly\. Nothing was removed\./],
  ['refused_already_deleted', /^Not deleted: its local data was already deleted\. Nothing was removed\./],
] as const) {
  test(`REFUSED (${name}): that row's own reason, never a generic failure`, async () => {
    const text = await deleteAnsweredWith(name);
    assert.equal(await result().getAttribute('data-state'), 'refused');
    assert.match(text, words);
    assert.match(text, /The request panel said: /);
  }, STEPS_MS);
}

test('UNCERTAIN: sent, and no answer came back, so it says not known, with why: never success, never refusal', async () => {
  const text = await deleteAnsweredWith('unknown_after_send');
  assert.equal(await result().getAttribute('data-state'), 'unknown');
  assert.match(text, /^It is not known whether the deletion happened: the request was sent and no usable answer came back\./);
  assert.doesNotMatch(text, /Not deleted|was deleted|Nothing was removed/);
}, STEPS_MS);

test('UNSTATED: an accepted answer that says neither outcome is never read as finished', async () => {
  answerWith = { ...replies.completed_after_reclamation!, answer: { ok: true } } as DiMissionRequestReply;
  await bootAtTheArchivedMission();
  await hold();
  await result().waitFor({ timeout: 8000 });
  assert.equal(await result().getAttribute('data-state'), 'unknown');
  assert.equal(((await result().textContent()) ?? '').trim(), MR_DELETE_UNSTATED_WORDS);
}, STEPS_MS);

test('A SECOND PRESS WHILE ONE IS IN FLIGHT: impossible (the hold is withdrawn), the waiting state is visible, and exactly one delete is sent', async () => {
  await bootAtTheArchivedMission();
  await hold();
  await result().waitFor({ timeout: 8000 });
  assert.equal(await result().getAttribute('data-state'), 'in_flight');
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0, 'the hold is withdrawn while it waits');
  assert.match((await p().locator('[data-testid="di-scan-trash-reason"]').textContent()) ?? '', /waiting for its answer, so it is not offered again/);
  await hold();                                                                         // the second press, on the withdrawn control
  await p().waitForTimeout(300);
  assert.equal(deletes.length, 1, 'no duplicate delete');
  answer(deletes[0]!, replies.pending_started_not_finished!);
  await p().waitForSelector(`[data-testid="di-scan-delete-result"][data-mission-ref="${REF}"][data-state="pending"]`, { timeout: 8000 });
  assert.equal(deletes.length, 1);
}, STEPS_MS);

test('TARGET IDENTITY: two sources share the native id; the outcome shows on the row acted on ONLY, and a store mismatch is refused there with its reason', async () => {
  const text = await deleteAnsweredWith('refused_source_mismatch');
  assert.equal(await result().getAttribute('data-state'), 'refused');
  assert.match(text, /^Not deleted: it belongs to a different Mission Core store than this request panel’s own, and a Mission is never resolved by its id alone\. Nothing was removed\./);
  assert.match(text, /The request panel said: /);
  // the twin row (same native id, other source) carries no outcome of it
  await openMission(TWIN_REF, true);
  assert.equal(await p().locator('[data-testid="di-scan-delete-result"]').count(), 0, 'nothing on the other source\u2019s row');
  // holding the twin sends ITS ref and ITS store, not the first row's
  answerWith = replies.pending_started_not_finished!;
  await hold();
  await p().waitForSelector(`[data-testid="di-scan-delete-result"][data-mission-ref="${TWIN_REF}"]`, { timeout: 8000 });
  assert.equal(deletes.length, 2);
  assert.deepEqual([deletes[1]!.missionRef, deletes[1]!.storeIdentity], [TWIN_REF, 'd'.repeat(64)]);
  // and the first row still shows its own refusal, not the twin's outcome
  await openMission(REF, true);
  assert.equal(await result().getAttribute('data-state'), 'refused');
}, STEPS_MS);
