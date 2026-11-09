/**
 * D1: leaving a picked Recent conversation on New Mission, as RENDERED in the real App composition (the request workspace's own state, its reducer, the
 * office panel and the scan face's Escape ladder), one layer at a time.
 *
 *  D1.1 every pick state (read with turns, read and empty, still being read, not read) draws ONE labelled way out, "Close this conversation"; using it
 *       returns New Mission to its blank start, keeps the typed ask exactly, sends nothing, and puts focus in the box;
 *  D1.2 the FIRST Escape does the same; the SECOND returns Home, with focus on the rail entry that opened New Mission;
 *  D1.3 + New Orchestrator and Cmd N never bring a closed conversation back, and nothing of it is read again;
 *  D1.4 (P3) a turn sent into a conversation, answered LATE after the person closed it (by the control or by Escape) or picked another: the closed one
 *       never returns and a newer draft is never wiped; staying on it, a draft typed while waiting is kept;
 *  D1.5 (P4) nested layers: the request workspace over New Mission owns Escape and closes alone; then the conversation; then Home. Focus never lands on
 *       the page body when the control that had it unmounts;
 *  D1.6 the thread's own "Start a new conversation" still clears the box (unchanged), and no longer drops focus to the page body.
 *
 * The office state is the session-mismatch v2 fixture (real projection output) in its labelled available-capacity variation. Every request-panel answer is
 * LABELLED SYNTHETIC (test/diNewMissionSynthetic.ts, the panel's own wire shape and the relay's own reply states). Nothing is live.
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
import { DI_ASK_BUSY } from '../src/components/diScanPagesModel.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  AUTHORITY_OPS,
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_OTHER_CONVERSATION_ID,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

const A = SYNTHETIC_CONVERSATION_ID;
const B = SYNTHETIC_OTHER_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A: the conversation that reads';
const ASK_B = 'SYNTHETIC recent ask B: the conversation picked in each state';
const TYPED = '  An unsent ask, typed after the pick ✓\nsecond line, two  spaces.  ';
const LABEL = 'Close this conversation';

type How = 'ok' | 'empty' | 'refused';
let recent: Record<string, How> = {};
/** Held replies: a read of a conversation, or a turn, answered only when released (so it lands after the person has moved on). */
let holdReads = new Set<string>();
let holdTurns = false;
let held: Array<{ key: string; fire: () => void }> = [];
/** Follow-up F1: the `seq` of every turn answer the synthetic panel actually DELIVERED, in order (an answer is matched to its turn by `seq`). */
let deliveredTurnSeqs: number[] = [];
function release(key: string): void {
  if (key === 'turn') holdTurns = false;
  else holdReads.delete(key);
  const now = held.filter((h) => h.key === key);
  held = held.filter((h) => h.key !== key);
  for (const h of now) h.fire();
}

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
        sentByPage.push(msg);
        if (msg.type !== 'diMissionRequest') return;
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview([syntheticConversationSummary(A, ASK_A), syntheticConversationSummary(B, ASK_B)])));
        if (msg.op === 'turn') {
          // the turn is answered as the panel answers it: one reserved turn OF THE CONVERSATION IT WAS SENT INTO. Pre-review 3: as the panel does, it is
          // a NEW turn of that conversation (its own id, the next sequence), so it JOINS the conversation rather than standing in for its existing turn
          const into = msg.conversationId ?? null;
          const fire = () => {
            deliveredTurnSeqs.push(Number(msg.seq));
            send(relayReply(msg.seq, 'turn', { ok: true, turn: syntheticTurn(String(msg.text), { conversation_id: into ?? A, turn_id: `ct-${'7'.repeat(32)}`, sequence: into === null ? 1 : 2 }) }));
          };
          if (holdTurns) held.push({ key: 'turn', fire });
          else fire();
        }
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          const fire = () => {
            const how = recent[id] ?? 'ok';
            if (how === 'ok') send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(id === A ? ASK_A : ASK_B)], id)));
            else if (how === 'empty') send(relayReply(msg.seq, 'conversation', syntheticConversation([], id)));
            else
              send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read.', answer: null });
          };
          if (holdReads.has(id)) held.push({ key: id, fire });
          else fire();
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
    console.warn(`SKIPPED (New Mission dismissal): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  recent = { [A]: 'ok', [B]: 'ok' };
  holdReads = new Set();
  holdTurns = false;
  held = [];
  deliveredTurnSeqs = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const ask = () => p().locator(sel('di-new-ask'));
const closeControl = () => p().locator(sel('di-new-close-conversation'));
const recentAsk = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const requests = () => sentByPage.filter((m) => m.type === 'diMissionRequest');
const turns = () => requests().filter((m) => m.op === 'turn');
const reads = (id: string) => requests().filter((m) => m.op === 'conversation' && m.conversationId === id).length;
const authority = () => requests().map((m) => String(m.op)).filter((op) => (AUTHORITY_OPS as readonly string[]).includes(op));
const view = () => p().locator(sel('di-scan-panel')).getAttribute('data-view');
/** The reason Send is held for, or null when it is not held (the page's own words). */
const sendReason = () => p().evaluate(() => document.querySelector('[data-testid="di-new-send-reason"]')?.textContent ?? null);
/**
 * Pre-review 3, tightened by pre-review 2: the released answer was ACKNOWLEDGED. Send was held for "waiting for the assistant" immediately before the release
 * (`busyBeforeRelease`), and now shows EXACTLY the hold the answer leads to: `expected` is that reason's words, or null when Send is offered with no hold.
 * Any other departure from busy (a missing element, a different hold) is not accepted as the answer having been consumed.
 */
const acknowledged = async (expected: string | null) => {
  // Follow-up F1: the answer delivered is the MATCHING one: exactly one turn answer, for the seq of the one turn the page sent
  assert.deepEqual(deliveredTurnSeqs, [Number(turns()[0]?.seq)], 'the only answer delivered is the one for the turn the page sent (matched by seq)');
  await p().waitForFunction(
    (want) => (document.querySelector('[data-testid="di-new-send-reason"]')?.textContent ?? null) === want,
    expected,
    { timeout: 5_000 },
  );
  assert.equal(
    await p().locator(sel('di-new-send')).getAttribute('aria-disabled'),
    expected === null ? null : 'true',
    `Send is ${expected === null ? 'offered' : 'held'} exactly as the answer leaves it`,
  );
};
/** Pre-review 2: busy is re-established AFTER any navigation, immediately before the answer is released. */
const busyBeforeRelease = async () => assert.equal(await sendReason(), DI_ASK_BUSY, 'immediately before the release, M1 is still being answered');
const threadText = async () => ((await p().locator(sel('di-new-thread')).count()) > 0 ? p().locator(sel('di-new-thread')).innerText() : '');
const focusNow = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName);
  });

async function boot(): Promise<void> {
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
  // Recent is drawn only from a READ overview: wait for it, so every pick below is a real one
  await recentAsk(A).waitFor({ timeout: 20_000 });
}
async function readsAnswered(id: string, n = 1): Promise<void> {
  const end = Date.now() + 10_000;
  while (reads(id) < n && Date.now() < end) await p().waitForTimeout(100);
  assert.ok(reads(id) >= n, `${n} read(s) of ${id.slice(0, 6)}`);
  await p().waitForTimeout(400);
}

type PickState = 'read with turns' | 'read and empty' | 'still being read' | 'not read';
const PICK_STATES: readonly PickState[] = ['read with turns', 'read and empty', 'still being read', 'not read'];
/** B picked from Recent and left in `state`, with an ask typed after the pick. */
async function picked(state: PickState): Promise<void> {
  if (state === 'still being read') holdReads.add(B);
  else recent[B] = state === 'read with turns' ? 'ok' : state === 'read and empty' ? 'empty' : 'refused';
  await boot();
  await recentAsk(B).click();
  await readsAnswered(B);
  if (state === 'read with turns') await p().waitForSelector(sel('di-new-thread'));
  if (state === 'read and empty') await p().waitForSelector(sel('di-new-thread-empty'));
  if (state === 'not read') await p().waitForSelector(sel('di-new-leave-unread'));
  assert.equal(await recentAsk(B).getAttribute('aria-current'), 'true', `precondition: B is the conversation shown (${state})`);
  await ask().fill(TYPED);
}
/** The blank start: no conversation current, nothing of one drawn, the ask as typed. */
async function assertBlank(why: string, kept = TYPED): Promise<void> {
  assert.equal(await view(), 'new', `${why}: still on New Mission`);
  assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, `${why}: no conversation is current`);
  for (const id of ['di-new-thread', 'di-new-thread-empty', 'di-new-leave-unread', 'di-new-close-conversation'])
    assert.equal(await p().locator(sel(id)).count(), 0, `${why}: ${id} is not drawn`);
  assert.equal(await p().locator('[data-testid="di-new-send-outcome"][data-op="conversation"]').count(), 0, `${why}: nothing is said about a conversation no longer shown`);
  assert.equal(await ask().inputValue(), kept, `${why}: the ask, exactly`);
}
/** + New Orchestrator, then (from Home) Cmd N: neither brings B back, and B is not read again. */
async function reentryKeepsItClosed(): Promise<void> {
  const before = reads(B);
  await p().click(sel('di-rail-home'));
  await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`);
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForTimeout(600);
  await assertBlank('after + New Orchestrator');
  await p().click(sel('di-rail-home'));
  await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`);
  await p().keyboard.press('Meta+n');
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForTimeout(600);
  await assertBlank('after Cmd N');
  assert.equal(reads(B), before, 'the closed conversation is not read again');
}

// ── D1.1 the labelled way out, in every pick state ───────────────────────────────────────────────────────────────────────────────────────

for (const state of PICK_STATES)
  test(
    `D1.1 (${state.toUpperCase()}): one labelled "Close this conversation" is drawn; using it returns to the blank start with the ask kept, sends nothing, focus in the box; re-entry never brings it back`,
    async () => {
      await picked(state);
      assert.equal(await closeControl().count(), 1, 'the labelled way out is drawn, once');
      assert.equal(((await closeControl().textContent()) ?? '').trim(), LABEL);
      assert.equal(await closeControl().getAttribute('aria-keyshortcuts'), 'Escape', 'it says Escape does the same');
      assert.match((await closeControl().getAttribute('title')) ?? '', /ask stays in the box/i);
      const before = requests().length;
      await closeControl().click();
      await p().waitForSelector(sel('di-new-close-conversation'), { state: 'detached' });
      await p().waitForTimeout(300);
      await assertBlank('after Close this conversation');
      assert.deepEqual(requests().slice(before).filter((m) => m.op !== 'refresh'), [], 'closing sends nothing');
      assert.equal(await focusNow(), 'di-new-ask', 'focus is in the box that kept the ask (the control that had it is gone)');
      // a read still in flight for it lands now: it does not bring it back
      if (state === 'still being read') {
        release(B);
        await p().waitForTimeout(800);
        await assertBlank('after its late read landed');
      }
      await reentryKeepsItClosed();
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'D1.1 by KEYBOARD: the way out is reached by Tab and used with Enter; focus ends in the box with the ask kept',
  async () => {
    await picked('read with turns');
    await ask().focus();
    for (let i = 0; i < 40 && (await focusNow()) !== 'di-new-close-conversation'; i++) await p().keyboard.press('Shift+Tab');
    assert.equal(await focusNow(), 'di-new-close-conversation', 'reached by keyboard');
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-new-close-conversation'), { state: 'detached' });
    await p().waitForTimeout(300);
    await assertBlank('after Enter');
    assert.equal(await focusNow(), 'di-new-ask');
  },
  STEPS_MS,
);

// ── D1.2 the Escape grammar: first Escape closes the conversation, second returns Home ────────────────────────────────────────────────────

for (const state of PICK_STATES)
  test(
    `D1.2 (${state.toUpperCase()}): the FIRST Escape closes the picked conversation (ask kept, focus in the box); the SECOND returns Home with focus on + New Orchestrator; re-entry never brings it back`,
    async () => {
      await picked(state);
      await ask().focus();
      const before = requests().length;
      await p().keyboard.press('Escape');
      await p().waitForTimeout(400);
      await assertBlank('after the first Escape');
      assert.equal(await focusNow(), 'di-new-ask');
      assert.deepEqual(requests().slice(before).filter((m) => m.op !== 'refresh'), [], 'the first Escape sends nothing');
      await p().keyboard.press('Escape');
      await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`, { timeout: 5_000 });
      assert.equal(await focusNow(), 'di-rail-new-orchestrator', 'focus is on the rail entry that opened New Mission');
      if (state === 'still being read') {
        release(B);
        await p().waitForTimeout(800);
      }
      await p().click(sel('di-rail-new-orchestrator'));
      await p().waitForSelector(sel('di-new-mission'));
      await p().waitForTimeout(600);
      await assertBlank('after + New Orchestrator');
      await p().keyboard.press('Escape');
      await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`, { timeout: 5_000 });
      await p().keyboard.press('Meta+n');
      await p().waitForSelector(sel('di-new-mission'));
      await p().waitForTimeout(600);
      await assertBlank('after Cmd N');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'D1.2 with nothing picked, Escape goes Home at once (one layer: there is no conversation to close), as before',
  async () => {
    await boot();
    await ask().fill(TYPED);
    await ask().focus();
    await p().keyboard.press('Escape');
    await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`, { timeout: 5_000 });
    assert.equal(await focusNow(), 'di-rail-new-orchestrator');
    await p().keyboard.press('Meta+n');
    await p().waitForSelector(sel('di-new-mission'));
    assert.equal(await ask().inputValue(), TYPED, 'the ask is kept across Home and back');
  },
  STEPS_MS,
);

test(
  'D1.2 Escape while focus is on a control INSIDE the thread (it unmounts with the conversation): focus lands in the box, never on the page body',
  async () => {
    await picked('read with turns');
    const inThread = p().locator(`${sel('di-new-thread')} button`).first();
    await inThread.focus();
    assert.notEqual(await focusNow(), 'BODY', 'precondition: focus is on a control in the thread');
    await p().keyboard.press('Escape');
    await p().waitForTimeout(400);
    await assertBlank('after Escape from inside the thread');
    assert.equal(await focusNow(), 'di-new-ask');
  },
  STEPS_MS,
);

// ── D1.4 (P3) a turn answered LATE ───────────────────────────────────────────────────────────────────────────────────────────────────────

/** A picked and read, M1 sent into it, its answer HELD. */
async function m1PendingInA(): Promise<void> {
  await boot();
  await recentAsk(A).click();
  await readsAnswered(A);
  await p().waitForSelector(sel('di-new-thread'));
  await ask().fill('M1, sent into A');
  holdTurns = true;
  await p().click(sel('di-new-send'));
  const end = Date.now() + 5_000;
  while (turns().length < 1 && Date.now() < end) await p().waitForTimeout(50);
  assert.equal(turns().length, 1);
  assert.equal(turns()[0].conversationId, A, 'precondition: M1 went into A');
  await p().waitForTimeout(200);
  assert.equal(await sendReason(), DI_ASK_BUSY, 'precondition: M1 is still being answered (Send is held for it)');
}
for (const how of ['the control', 'Escape'] as const)
  test(
    `D1.4 (P3), A closed by ${how} while M1 is being answered, a newer draft typed, THEN the answer lands: A does not come back and the newer draft is kept; re-entry keeps it closed`,
    async () => {
      await m1PendingInA();
      if (how === 'the control') await closeControl().click();
      else {
        await ask().focus();
        await p().keyboard.press('Escape');
      }
      await p().waitForTimeout(400);
      assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'A is closed');
      await ask().fill('M2, a newer draft');
      const readsOfA = reads(A);
      await busyBeforeRelease();
      release('turn');
      await acknowledged(null);
      await p().waitForTimeout(300);
      assert.equal(await view(), 'new');
      assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'A did not come back');
      assert.equal(await p().locator(sel('di-new-thread')).count(), 0, 'no thread is drawn');
      assert.equal(await ask().inputValue(), 'M2, a newer draft', 'the newer draft is kept');
      assert.equal(reads(A), readsOfA, 'A is not read again');
      assert.equal(turns().length, 1, 'nothing was sent again');
      await p().click(sel('di-rail-home'));
      await p().keyboard.press('Meta+n');
      await p().waitForSelector(sel('di-new-mission'));
      await p().waitForTimeout(600);
      assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'Cmd N does not bring A back');
      assert.equal(await ask().inputValue(), 'M2, a newer draft');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'D1.4 (P3), A closed while M1 is being answered and the box left as it was: when the answer lands, A stays closed and M1 (now recorded in A) is not left in the box to be sent twice',
  async () => {
    await m1PendingInA();
    await closeControl().click();
    await p().waitForTimeout(400);
    assert.equal(await ask().inputValue(), 'M1, sent into A', 'closing kept the box as it was');
    await busyBeforeRelease();
    release('turn');
    await acknowledged('Type an ask first.');
    await p().waitForTimeout(300);
    assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'A did not come back');
    assert.equal(await ask().inputValue(), '', 'the words that were recorded are cleared, as a Send that was accepted clears them');
    assert.equal(turns().length, 1);
  },
  STEPS_MS,
);

test(
  'D1.4 (P3), B picked while M1 (sent into A) is being answered, a draft typed for B, THEN the answer lands: B stays shown and its draft is kept',
  async () => {
    await m1PendingInA();
    await recentAsk(B).click();
    await readsAnswered(B);
    await ask().fill('M2, for B');
    await busyBeforeRelease();
    release('turn');
    await acknowledged(null);
    await p().waitForTimeout(300);
    assert.equal(await recentAsk(B).getAttribute('aria-current'), 'true', 'B is still the conversation shown');
    assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'A did not replace it');
    assert.match(await p().locator(sel('di-new-thread')).innerText(), new RegExp(ASK_B), 'the thread is B');
    assert.equal(await ask().inputValue(), 'M2, for B', 'the draft for B is kept');
  },
  STEPS_MS,
);

test(
  'D1.4 (P3), STAYING on A while M1 is answered, a follow-up typed meanwhile: the answer joins A and the follow-up is kept (a reply never wipes newer words)',
  async () => {
    await m1PendingInA();
    await ask().fill('M2, typed while waiting');
    assert.doesNotMatch(await threadText(), /M1, sent into A/, 'precondition: the answer has not joined A yet');
    // A's later READS are held, so what the thread shows below is what the ANSWER drew (a read of the record would also show it, and would hide a dropped answer)
    holdReads.add(A);
    await busyBeforeRelease();
    release('turn');
    // pre-review 3: the DISTINCTIVE returned turn joins A (dropping the answer would leave the thread without it)
    await p().waitForFunction(() => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes('M1, sent into A') === true, undefined, { timeout: 5_000 });
    assert.match(await threadText(), /M1, sent into A/, "the answer joined A's thread");
    assert.equal(await recentAsk(A).getAttribute('aria-current'), 'true', 'A is still shown');
    assert.equal(await ask().inputValue(), 'M2, typed while waiting');
  },
  STEPS_MS,
);

// ── D1.5 (P4) nested layers, one at a time ───────────────────────────────────────────────────────────────────────────────────────────────

test(
  'D1.5 (P4): the request workspace opened on Ask over a picked conversation owns Escape, one layer at a time: the FIRST closes the conversation it shows (inside the workspace: the composer keeps the draft); the SECOND closes the workspace (its opener unmounted with the conversation: focus lands on New Mission\'s heading, never the page body); the next returns Home',
  async () => {
    await boot();
    await recentAsk(A).click();
    await readsAnswered(A);
    await p().waitForSelector(sel('di-new-draft-open'));
    await p().click(sel('di-new-draft-open'));
    await p().waitForSelector(sel('di-mr-composer'));
    await p().locator(sel('di-mr-composer')).fill(TYPED);
    await p().locator(sel('di-mr-heading')).focus();
    // layer 1: the conversation the workspace shows (it is the topmost owner of the key)
    await p().keyboard.press('Escape');
    await p().waitForTimeout(400);
    assert.equal(await p().locator(sel('di-mr-composer')).count(), 1, 'the workspace is still open');
    assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'A is closed (the ONE conversation the workspace and New Mission share)');
    assert.equal(await focusNow(), 'di-mr-composer', 'focus is in the workspace composer');
    // layer 2: the workspace
    await p().keyboard.press('Escape');
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached', timeout: 5_000 });
    await p().waitForTimeout(300);
    await assertBlank('after the second Escape');
    assert.equal(
      await p().evaluate(() => document.activeElement === document.querySelector('[data-testid="di-new-mission"] [data-page-heading="true"]')),
      true,
      `focus is on New Mission's heading (it is on ${await focusNow()})`,
    );
    // layer 3: New Mission itself
    await p().keyboard.press('Escape');
    await p().waitForSelector(`${sel('di-scan-panel')}[data-view="list"]`, { timeout: 5_000 });
    assert.equal(await focusNow(), 'di-rail-new-orchestrator');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

test(
  'D1.5 (P4): Escape typed INSIDE the workspace never reaches New Mission: one press closes the workspace only, however many layers are below',
  async () => {
    await picked('read with turns');
    await p().click(sel('di-new-open-workspace'));
    await p().waitForSelector(sel('di-mr-heading'));
    await p().locator(sel('di-mr-heading')).focus();
    await p().keyboard.press('Escape');
    await p().waitForSelector(sel('di-mr-heading'), { state: 'detached', timeout: 5_000 });
    await p().waitForTimeout(300);
    assert.equal(await recentAsk(B).getAttribute('aria-current'), 'true', 'the conversation under it is still shown');
    assert.equal(await closeControl().count(), 1, 'and its way out is still there');
    assert.equal(await focusNow(), 'di-new-open-workspace', 'focus is back on the workspace opener');
  },
  STEPS_MS,
);

// ── D1.6 the thread's own reset, unchanged except for focus ──────────────────────────────────────────────────────────────────────────────

test(
  'D1.6: the thread\'s own "Start a new conversation" still clears the box (unchanged) and sends nothing; focus lands in the box, never on the page body',
  async () => {
    await picked('read with turns');
    const before = requests().length;
    await p().locator(sel('di-new-new-conversation')).click();
    await p().waitForSelector(sel('di-new-thread'), { state: 'detached' });
    await p().waitForTimeout(300);
    await assertBlank('after the thread\'s Start a new conversation', '');
    assert.deepEqual(requests().slice(before).filter((m) => m.op !== 'refresh'), []);
    assert.equal(await focusNow(), 'di-new-ask', 'focus is in the box, not on <body>');
  },
  STEPS_MS,
);
