/**
 * New Mission across the states a person can meet without navigating: the request panel becoming available or unavailable, a Send that is refused or whose outcome
 * is not known, and where focus lands when New Mission is entered while the request workspace is open (Free UI streamline, review round 1: F1, F2, F4).
 *
 *  F1  the ONE composer holds the person's unsent ask across availability changes, with an empty and with a different existing draft: the exact text, one visible box,
 *      nothing written, kept across leaving and coming back;
 *  F2  a refused, unknown or unavailable Send is SAID on New Mission (an accessible outcome with its reason), the ask is kept exactly, nothing is retried, and an
 *      unknown outcome is said as not known (never as refused, done or "nothing changed");
 *  F4  entering New Orchestrator (pointer, keyboard) or pressing Cmd N while the workspace is open puts focus on the entry used, never on the control that opened the
 *      workspace earlier; "Bring the ask back here" puts it in the box.
 *
 * The office state is the session-mismatch v2 fixture (real projection output) in its labelled available-capacity variation. Every request-panel answer is LABELLED
 * SYNTHETIC (test/diNewMissionSynthetic.ts, the panel's own wire shape and the relay's own reply states). Nothing is live.
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
import { problemWords } from '../src/components/diMissionRequestModel.ts';
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
import { escapeRegExp } from './helpers/markupText.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

const TEXT = '  An unsent ask — typed while the panel was not available.\nSecond line: ünïcödé ✓, two  spaces.  ';
const OTHER = 'An existing unsent draft, typed in the workspace ✓';
const UNAVAILABLE_REASON = 'SYNTHETIC: the request panel’s discovery file could not be read.';

/** How the stand-in panel answers. */
interface Answers {
  /** The overview: answered as available, or as the relay answers when the panel is unavailable. */
  panel: 'available' | 'unavailable';
  /** How a Send (`turn`) is answered: drafted, refused by the panel, outcome unknown, or unavailable. */
  turn: 'ok' | 'refused' | 'unknown' | 'unavailable';
  /** Review round 2, blocker 1: when set, the overview lists two recent conversations, A and B, and each one's READ is answered as given. */
  recent?: Record<string, 'ok' | 'empty' | 'refused' | 'unknown'>;
  /** When set, that recent conversation is also a Reception entry waiting for an answer. */
  receptionAwaiting?: string;
}
let answers: Answers = { panel: 'available', turn: 'ok' };
const A = SYNTHETIC_CONVERSATION_ID;
const B = SYNTHETIC_OTHER_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A: the conversation that reads';
const ASK_B = 'SYNTHETIC recent ask B: the conversation whose read is answered otherwise';
/** Identity proofs: a conversation's read replies can be HELD and released later, so a reply lands after the person has moved on. */
let holdReads = new Set<string>();
let held: Array<{ id: string; fire: () => void }> = [];
function release(id: string): void {
  holdReads.delete(id);
  const now = held.filter((h) => h.id === id);
  held = held.filter((h) => h.id !== id);
  for (const h of now) h.fire();
}
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
        if (msg.op === 'refresh') {
          if (answers.panel === 'available' && answers.recent) {
            const summaries = [syntheticConversationSummary(A, ASK_A), syntheticConversationSummary(B, ASK_B)].map((summary) => {
              const id = String((summary as { conversation_id?: unknown }).conversation_id ?? '');
              if (answers.receptionAwaiting !== id) return summary;
              return {
                ...summary,
                awaiting_answer: true,
                awaiting_turn_id: `ct-${'a'.repeat(32)}`,
                awaiting_questions: ['SYNTHETIC: what should the next step be?'],
              };
            });
            send(relayReply(msg.seq, 'refresh', syntheticOverview(summaries)));
          } else if (answers.panel === 'available') send(relayReply(msg.seq, 'refresh', syntheticOverview()));
          else send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'refresh', state: 'unavailable', problem: 'panel_discovery_unavailable', reason: UNAVAILABLE_REASON, answer: null });
        }
        if (msg.op === 'turn') {
          if (answers.turn === 'ok') {
            sentTurn = String(msg.text);
            send(relayReply(msg.seq, 'turn', { ok: true, turn: syntheticTurn(sentTurn) }));
          } else if (answers.turn === 'refused')
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'turn', state: 'refused', problem: 'mission_panel_conversation_busy', reason: 'SYNTHETIC: the conversation is busy with another message.', answer: null });
          else if (answers.turn === 'unknown')
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'turn', state: 'unknown', problem: 'panel_timeout', reason: 'SYNTHETIC: the panel did not answer in time.', answer: null });
          else send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'turn', state: 'unavailable', problem: 'panel_discovery_unavailable', reason: UNAVAILABLE_REASON, answer: null });
        }
        if (msg.op === 'conversation' && answers.recent) {
          const id = String(msg.conversationId);
          const fire = () => {
          const how = answers.recent?.[id] ?? 'ok';
          if (how === 'ok') send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(id === A ? ASK_A : ASK_B)], id)));
          else if (how === 'empty') send(relayReply(msg.seq, 'conversation', syntheticConversation([], id)));
          else if (how === 'refused')
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read.', answer: null });
          else send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'unknown', problem: 'panel_timeout', reason: 'SYNTHETIC: the panel did not answer the read in time.', answer: null });
          };
          if (holdReads.has(id)) held.push({ id, fire });
          else fire();
        } else if (msg.op === 'conversation' && sentTurn !== null) send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(sentTurn)])));
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
    console.warn(`SKIPPED (New Mission states): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  sentTurn = null;
  answers = { panel: 'available', turn: 'ok' };
  holdReads = new Set();
  held = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const ops = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op));
const turns = () => sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op === 'turn');
const authority = () => ops().filter((op) => (AUTHORITY_OPS as readonly string[]).includes(op));
const ask = () => p().locator(sel('di-new-ask'));
const send = () => p().locator(sel('di-new-send'));
const focused = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);

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
}
/**
 * Wait until the page has READ the panel as `panel`: an overview read answered in that state, and the page drawing it. The sign drawn is independent of the
 * composer (the box is what is under test): Recent says "not read" exactly while there is no overview, i.e. while the panel is unavailable.
 */
async function panelBecomes(panel: Answers['panel']): Promise<void> {
  answers.panel = panel;
  const before = ops().filter((op) => op === 'refresh').length;
  const end = Date.now() + 20_000;
  while (ops().filter((op) => op === 'refresh').length <= before) {
    if (Date.now() > end) throw new Error('no overview read');
    await new Promise((r) => setTimeout(r, 100));
  }
  await p().waitForSelector(sel('di-new-recent-not-read'), { state: panel === 'unavailable' ? 'attached' : 'detached', timeout: 10_000 });
}
const visibleComposers = () =>
  p().evaluate(() =>
    ['di-new-ask', 'di-mr-composer'].filter((id) => {
      const el = document.querySelector<HTMLTextAreaElement>(`[data-testid="${id}"]`);
      if (el === null || el.getClientRects().length === 0 || el.readOnly || el.disabled) return false;
      const r = el.getBoundingClientRect();
      const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return at === el || (at !== null && el.contains(at));
    }),
  );

// ── F1 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

test(
  'F1, EMPTY existing draft: an ask typed while the panel is NOT available stays, exactly, in the one box when the panel becomes available, across leaving and coming back, and when it goes unavailable again; nothing is written',
  async () => {
    answers.panel = 'unavailable';
    await boot();
    await panelBecomes('unavailable');
    await ask().fill(TEXT);
    // precondition, read AFTER typing: the empty-ask guard comes first, so only a typed ask shows the panel's own hold
    assert.match((await send().getAttribute('title')) ?? '', /Mission requests are not available/, 'precondition: Send is held because the panel is not available');
    // round-2 sweep (F1's neighbour): Review requests is held with the SAME condition as Send, "not available now", never "not available in this office"
    assert.equal(await p().locator(sel('di-new-open-workspace')).getAttribute('title'), 'Mission requests are not available now');
    await panelBecomes('available');
    assert.equal(await ask().inputValue(), TEXT, 'the exact ask, after the panel became available');
    assert.equal(await send().getAttribute('aria-disabled'), null, 'and it can be sent now');
    assert.equal(await p().locator(sel('di-new-open-workspace')).getAttribute('title'), null, 'and Review requests is offered again');
    assert.deepEqual(await visibleComposers(), ['di-new-ask'], 'one visible box');
    // leaving New Mission and coming back
    await p().click(sel('di-rail-missions'));
    await p().waitForSelector(sel('di-missions-page'));
    await p().click(sel('di-rail-new-orchestrator'));
    await p().waitForSelector(sel('di-new-mission'));
    assert.equal(await ask().inputValue(), TEXT, 'the exact ask, after leaving and coming back');
    await panelBecomes('unavailable');
    assert.equal(await ask().inputValue(), TEXT, 'the exact ask, when the panel goes unavailable again');
    assert.deepEqual(authority(), []);
    assert.equal(turns().length, 0, 'nothing was sent');
  },
  STEPS_MS,
);

test(
  'F1, a DIFFERENT existing draft: the one box is that draft whatever the panel\'s availability; what is typed into it while unavailable is the same draft, kept exactly when the panel comes back; nothing is written',
  async () => {
    await boot();
    await panelBecomes('available');
    await p().click(sel('di-new-open-workspace'));
    await p().click(sel('di-mr-tab-ask'));
    await p().locator(sel('di-mr-composer')).fill(OTHER);
    await p().locator(sel('di-mr-composer')).focus();
    await p().keyboard.press('Escape');
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached' });
    assert.equal(await ask().inputValue(), OTHER);
    await panelBecomes('unavailable');
    assert.equal(await ask().inputValue(), OTHER, 'the existing draft is still the box, while the panel is unavailable');
    await ask().press('End');
    await ask().pressSequentially(' + typed while unavailable');
    await panelBecomes('available');
    assert.equal(await ask().inputValue(), `${OTHER} + typed while unavailable`, 'the exact text, after the panel came back');
    assert.deepEqual(await visibleComposers(), ['di-new-ask']);
    assert.deepEqual(authority(), []);
    assert.equal(turns().length, 0);
  },
  STEPS_MS,
);

// ── F2 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

for (const [kind, said, notSaid] of [
  // a refusal the panel names is said in the workspace's own words for that problem (the reducer's problemWords, unchanged by this task), exactly
  ['refused', new RegExp(`^Refused\\. ${escapeRegExp(problemWords('mission_panel_conversation_busy', 'SYNTHETIC: the conversation is busy with another message.', 'turn'))}`), /not known/],
  ['unknown', /Outcome not known\.[\s\S]*did not answer in time/, /Refused|Not sent|nothing changed|Nothing was changed/i],
  ['unavailable', /Not sent\.|not available/, /Outcome not known/],
] as const)
  test(
    `F2, a Send answered ${kind.toUpperCase()}: New Mission says so itself (an accessible outcome with its reason), keeps the exact ask, and retries nothing`,
    async () => {
      await boot();
      await panelBecomes('available');
      answers.turn = kind;
      await ask().fill(TEXT);
      await send().click();
      const outcome = p().locator(sel('di-new-send-outcome'));
      await outcome.waitFor({ timeout: 10_000 });
      const words = (await outcome.innerText()).trim();
      assert.match(words, said, `the outcome and its reason: ${words}`);
      assert.doesNotMatch(words, notSaid, `never said as something else: ${words}`);
      assert.equal(await outcome.evaluate((el) => el.closest('[role="status"]') !== null), true, 'announced (inside a status region)');
      assert.equal(await ask().inputValue(), TEXT, 'the exact ask is kept');
      await p().waitForTimeout(1500);
      assert.equal(turns().length, 1, 'no automatic retry');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'F3a, Dismiss on a Send outcome BY KEYBOARD: the outcome goes and focus lands in the ask box, which is SEEN (never on the page); the typed ask is kept',
  async () => {
    await boot();
    await panelBecomes('available');
    answers.turn = 'refused';
    await ask().fill(TEXT);
    await send().click();
    const outcome = p().locator(sel('di-new-send-outcome'));
    await outcome.waitFor({ timeout: 10_000 });
    const problems: string[] = [];
    await p().locator(sel('di-new-send-outcome-dismiss')).first().focus();
    await p().keyboard.press('Enter');
    await outcome.first().waitFor({ state: 'detached', timeout: 5_000 });
    const focused = p().locator(':focus');
    const landed = (await focused.count()) === 0 ? 'BODY' : ((await focused.first().getAttribute('data-testid')) ?? 'an element without a test id');
    if (landed !== 'di-new-ask') problems.push(`focus landed on ${landed}, not in the ask box`);
    const box = await ask().boundingBox();
    const view = p().viewportSize();
    let seenOk = false;
    if (box !== null && view !== null && box.width > 0 && box.height > 0 && box.y >= 0 && box.y + box.height <= view.height) {
      try {
        await ask().click({ trial: true, timeout: 1500 });
        seenOk = true;
      } catch {
        seenOk = false;
      }
    }
    if (!seenOk) problems.push('the ask box is not SEEN (not in the window or not hit-testable at its centre)');
    if ((await ask().inputValue()) !== TEXT) problems.push('the typed ask was not kept exactly');
    assert.deepEqual(problems, [], 'CONTRACT (DISMISS LANDING): the outcome goes, focus lands in the ask box (seen), and the ask is kept');
  },
  STEPS_MS,
);

// ── F4 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

/** The workspace open on Ask, opened from "Review requests", with an unsent draft. */
async function workspaceOpenFromReviewRequests(): Promise<void> {
  await boot();
  await panelBecomes('available');
  await p().click(sel('di-new-open-workspace'));
  await p().click(sel('di-mr-tab-ask'));
  await p().locator(sel('di-mr-composer')).fill(OTHER);
}
for (const route of ['pointer', 'keyboard'] as const)
  test(
    `F4, entering New Mission by ${route} while the workspace (opened from Review requests) is open: focus lands on the entry used, the draft is kept, nothing is sent`,
    async () => {
      await workspaceOpenFromReviewRequests();
      if (route === 'pointer') await p().click(sel('di-rail-new-orchestrator'));
      else {
        await p().locator(sel('di-rail-new-orchestrator')).focus();
        await p().keyboard.press('Enter');
      }
      await p().waitForSelector(sel('di-mr-composer'), { state: 'detached' });
      await p().waitForTimeout(200);
      assert.equal(await focused(), 'di-rail-new-orchestrator', `focus is on the entry used (${route})`);
      assert.equal(await ask().inputValue(), OTHER, 'the draft is kept');
      assert.deepEqual(authority(), []);
      assert.equal(turns().length, 0);
    },
    STEPS_MS,
  );

test(
  'F4, Cmd N pressed OUTSIDE the workspace (on the page) while it is open (opened from Review requests): it closes, the draft is kept, focus stays where the person was (never the earlier opener, never the page body), nothing is sent',
  async () => {
    await workspaceOpenFromReviewRequests();
    const heading = p().locator('[data-testid="di-new-mission"] [data-page-heading="true"]');
    await heading.focus();
    await p().keyboard.press('Meta+n');
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached', timeout: 10_000 });
    await p().waitForTimeout(200);
    assert.equal(await heading.evaluate((el) => el === document.activeElement), true, `focus stays on the page heading (it is on ${await focused()})`);
    assert.equal(await ask().inputValue(), OTHER, 'the draft is kept');
    assert.deepEqual(authority(), []);
    assert.equal(turns().length, 0);
  },
  STEPS_MS,
);

test(
  'F4, Cmd N typed INSIDE the workspace: the key reaches the page, and the workspace keeps it (its documented rule: keys typed in it never reach the office behind), so it stays open with the draft; nothing is sent',
  async () => {
    await workspaceOpenFromReviewRequests();
    await p().evaluate(() => {
      const w = window as unknown as { __keys: string[] };
      w.__keys = [];
      window.addEventListener('keydown', (e) => w.__keys.push(`${e.metaKey ? 'Meta+' : ''}${e.key}`), true);
    });
    await p().locator(sel('di-mr-composer')).focus();
    await p().keyboard.press('Meta+n');
    await p().waitForTimeout(1000);
    assert.ok((await p().evaluate(() => (window as unknown as { __keys: string[] }).__keys)).includes('Meta+n'), 'the browser delivered Meta+n to the page (seen in the capture phase)');
    assert.equal(await p().locator(sel('di-mr-composer')).count(), 1, 'the workspace is still open');
    assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), OTHER, 'its draft is kept');
    assert.deepEqual(authority(), []);
    assert.equal(turns().length, 0);
  },
  STEPS_MS,
);

test(
  'F4, "Bring the ask back here" closes the workspace and puts focus in the box, with the draft kept',
  async () => {
    await boot();
    await panelBecomes('available');
    await ask().fill(TEXT);
    await send().click();
    await p().waitForSelector(sel('di-new-draft-open'), { timeout: 20_000 });
    await p().click(sel('di-new-draft-open'));
    await p().waitForSelector(sel('di-mr-composer'));
    await p().locator(sel('di-mr-composer')).fill(OTHER);
    await p().click(sel('di-new-ask-bring-back'));
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached' });
    await p().waitForTimeout(200);
    assert.equal(await focused(), 'di-new-ask', 'focus is in the box');
    assert.equal(await ask().inputValue(), OTHER, 'the draft is kept');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

test(
  'F4, a REMOVED opener: the workspace opened from the draft\'s onward control; the person leaves New Mission (so that control is gone) and closes the workspace with its own Close: focus is not dropped to the page',
  async () => {
    await boot();
    await panelBecomes('available');
    await ask().fill(TEXT);
    await send().click();
    await p().waitForSelector(sel('di-new-draft-open'), { timeout: 20_000 });
    await p().click(sel('di-new-draft-open'));
    await p().waitForSelector(sel('di-mr-composer'));
    // the person leaves New Mission for Missions (the rail is beside the workspace at 1440 x 900): New Mission, and the opener with it, unmounts
    await p().click(sel('di-rail-missions'));
    await p().waitForSelector(sel('di-missions-page'));
    assert.equal(await p().locator(sel('di-new-draft-open')).count(), 0, 'the opener is gone');
    await p().click(sel('di-mr-close'));
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached' });
    await p().waitForTimeout(200);
    const at = await focused();
    assert.notEqual(at, 'BODY', `focus is not dropped to the page body (it is on ${at})`);
    assert.equal(
      await p().evaluate(() => document.activeElement === document.querySelector('[data-testid="di-missions-page"] [data-page-heading="true"]')),
      true,
      `focus is on the heading of the page now shown (Missions); it is on ${at}`,
    );
  },
  STEPS_MS,
);

// ── F4 (review round 2): where focus lands when the workspace closes while HOME or MISSION DETAILS is shown and its opener is gone ──────────────────────────
// Measured, not assumed (Reviewer round 2: the page-heading fallback exists on New Mission, Missions and Reception but not on Home or Mission details).

/** Where focus is: its testid, else its tag, and whether that is the page body. */
const focusNow = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return { at: a?.getAttribute('data-testid') ?? a?.tagName ?? null, body: a === null || a === document.body, view: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') ?? null };
  });
/** Move to a page by keyboard (focus the control, Enter): the workspace can cover part of the stage, and the keyboard route is the one every person has. */
async function go(testid: string): Promise<void> {
  await p().locator(sel(testid)).first().focus();
  await p().keyboard.press('Enter');
}
for (const [page, reach, view, target] of [
  ['HOME', async () => go('di-rail-home'), 'list', 'di-rail-home'],
  ['MISSION DETAILS', async () => {
    await go('di-rail-home');
    // CHANGED IN B2a (Q8 = (a)): one click docks a Mission; the select-then-open second step (the removed card's Open Mission) is gone
    await go('di-scan-row');
  }, 'mission', 'di-scan-back'],
] as const)
  for (const close of ['its Close button', 'Escape'] as const)
    test(
      `F4, the workspace opened from New Mission's Review requests, the person goes to ${page} (the opener unmounts), and closes it with ${close}: focus lands on the page shown, never <body>`,
      async () => {
        await boot();
        await panelBecomes('available');
        await p().click(sel('di-new-open-workspace'));
        await p().waitForSelector(sel('di-mr-close'));
        await reach();
        await p().waitForSelector(`[data-testid="di-scan-panel"][data-view="${view}"]`);
        assert.equal(await p().locator(sel('di-new-open-workspace')).count(), 0, 'the opener is gone');
        if (close === 'Escape') {
          await p().locator(sel('di-mr-heading')).focus();
          await p().keyboard.press('Escape');
        } else await p().locator(sel('di-mr-close')).click();
        await p().waitForSelector(sel('di-mr-close'), { state: 'detached' });
        await p().waitForTimeout(300);
        const now = await focusNow();
        console.log(`[F4 measure] ${page} / ${close}: ${JSON.stringify(now)}`);
        assert.equal(now.body, false, `focus is not dropped to the page body on ${page} (it is on ${now.at})`);
        // the page now shown: Home's current rail entry; Mission details' Back control (the scan face's own rescue target on a page change)
        assert.equal(now.at, target, `focus is on ${target}`);
        assert.deepEqual(authority(), []);
      },
      STEPS_MS,
    );

// ── Review round 2, BLOCKER 1: a READ that is refused, or whose answer is not known, on the conversation New Mission shows ───────────────────────────────────
// Recent (a click) and the watch (the poll while New Mission is shown) are New Mission's reads, added by this task. Neither may leave the page presenting a
// conversation it could not read as the current one, silently.

const conversationReads = (id: string) => sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op === 'conversation' && m.conversationId === id).length;
const readOutcomes = () => p().locator('[data-testid="di-new-send-outcome"][data-op="conversation"]');
/** New Mission showing conversation A, read from Recent. */
async function showingA(): Promise<void> {
  answers = { panel: 'available', turn: 'ok', recent: { [A]: 'ok', [B]: 'ok' } };
  await boot();
  await panelBecomes('available');
  await p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${A}"]`).click();
  await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, ASK_A);
}
for (const how of ['refused', 'unknown'] as const)
  test(
    `BLOCKER 1, a Recent click whose read is ${how.toUpperCase()}: New Mission says the conversation was not read, with the reason; it never shows another conversation as that one; Send will not send into a conversation it could not show`,
    async () => {
      await showingA();
      answers.recent![B] = how;
      await p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${B}"]`).click();
      await readOutcomes().first().waitFor({ timeout: 10_000 });
      const words = ((await readOutcomes().first().innerText()) ?? '').trim();
      assert.match(words, /^Not read\./, `said as not read: ${words}`);
      assert.match(words, how === 'refused' ? /could not be read/ : /did not answer the read in time/, `with the reason: ${words}`);
      assert.equal(await readOutcomes().first().evaluate((el) => el.closest('[role="status"]') !== null), true, 'announced');
      // A's words are not presented as B
      assert.doesNotMatch((await p().locator('[data-testid="di-new-mission"]').innerText()).replace(/Recent[\s\S]*$/, ''), new RegExp(ASK_A), 'A is not shown as the current conversation');
      // Send into B, which was never shown: held, with its reason, and nothing is sent
      await ask().fill(TEXT);
      assert.match((await send().getAttribute('title')) ?? '', /could not be read/, 'Send says why it is held');
      await send().click({ force: true });
      await p().waitForTimeout(500);
      assert.equal(turns().length, 0, 'nothing was sent into the unread conversation');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'BLOCKER 1, the WATCH read refused while conversation A is shown: New Mission says the conversation could not be read again (the thread is the last one read), ONCE however many polls are refused',
  async () => {
    await showingA();
    answers.recent![A] = 'refused';
    const before = conversationReads(A);
    const end = Date.now() + 20_000;
    while (conversationReads(A) < before + 2 && Date.now() < end) await p().waitForTimeout(200);
    assert.ok(conversationReads(A) >= before + 2, 'at least two watch reads were answered refused');
    await p().waitForTimeout(500);
    await readOutcomes().first().waitFor({ timeout: 5_000 });
    assert.equal(await readOutcomes().count(), 1, 'said once, not once per poll');
    const words = ((await readOutcomes().first().innerText()) ?? '').trim();
    assert.match(words, /^Not read\./, words);
    assert.match(words, /last read/i, `it says the thread shown is the last one read: ${words}`);
    assert.match(await p().locator(sel('di-new-thread')).innerText(), new RegExp(ASK_A), 'the last-read thread is still shown, under that statement');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

// ── Review round 2 (Lead1's round-3 requirement): RECOVERY, not only failure. "The read failed" and "the conversation is empty" are different facts; the page
// says each from explicit state (a successful read sets `readAt`; emptiness never stands for success). Each proof is run on BOTH routes separately: the Recent
// CLICK (a pick) and the WATCH refresh (the poll while New Mission shows the conversation).
// B2e: a RE-read of the conversation already shown is no longer a click on its Recent ask (the selected ask now CLOSES the conversation, as "Close this
// conversation" does); the CLICK route of a re-read is the labelled "Read this conversation again", drawn while the read failed. Assertions unchanged.

const recentAsk = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const readAgain = () => p().locator(sel('di-new-read-again'));
/** Wait until the page has made at least `n` more reads of conversation `id` (each answered as `answers.recent` says), then let it draw. */
async function reads(id: string, n: number): Promise<void> {
  const before = conversationReads(id);
  const end = Date.now() + 20_000;
  while (conversationReads(id) < before + n && Date.now() < end) await p().waitForTimeout(150);
  assert.ok(conversationReads(id) >= before + n, `${n} more read(s) of ${id.slice(0, 6)}`);
  await p().waitForTimeout(400);
}
/** B picked from Recent with its read refused: "not read", and Send held. */
async function bPickedAndRefused(): Promise<void> {
  await showingA();
  answers.recent![B] = 'refused';
  await recentAsk(B).click();
  await readOutcomes().first().waitFor({ timeout: 10_000 });
}
const sendHoldIsUnread = async () => /could not be read/.test((await send().getAttribute('title')) ?? '');

for (const route of ['CLICK', 'WATCH'] as const)
  test(
    `RECOVERY 1 (${route}): a refused read of a picked conversation, THEN a successful read of it, EMPTY: the page says it was read and holds no messages, not "not read", and Send is no longer held for it`,
    async () => {
      await bPickedAndRefused();
      await ask().fill(TEXT);
      assert.equal(await sendHoldIsUnread(), true, 'precondition: held as not read');
      answers.recent![B] = 'empty';
      if (route === 'CLICK') await readAgain().click();
      await reads(B, 1);
      await p().waitForSelector(sel('di-new-thread-empty'), { timeout: 10_000 });
      assert.match(await p().locator(sel('di-new-thread-empty')).innerText(), /was read: it holds no messages/);
      assert.equal(await readOutcomes().count(), 0, 'the "not read" claim is withdrawn');
      assert.equal(await sendHoldIsUnread(), false, 'Send is no longer held as not read');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

for (const route of ['CLICK', 'WATCH'] as const)
  test(
    `RECOVERY 2 (${route}): A shown and read, its reads then REFUSED, then a successful read of A: the "not read" claim is withdrawn (a refusal does not outlive the state it described)`,
    async () => {
      await showingA();
      answers.recent![A] = 'refused';
      await reads(A, 1);
      await readOutcomes().first().waitFor({ timeout: 10_000 });
      assert.match(await readOutcomes().first().innerText(), /as last read/);
      answers.recent![A] = 'ok';
      if (route === 'CLICK') await readAgain().click();
      await reads(A, 1);
      await p().waitForFunction(() => document.querySelectorAll('[data-testid="di-new-send-outcome"][data-op="conversation"]').length === 0, undefined, { timeout: 10_000 });
      assert.match(await p().locator(sel('di-new-thread')).innerText(), new RegExp(ASK_A), 'A as read');
      assert.equal(await sendHoldIsUnread(), false);
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

for (const route of ['CLICK', 'WATCH'] as const)
  test(
    `RECOVERY 3 (${route}): after a refused read, the person leaves New Mission and comes back: no old notice is said under the conversation, and no stale "not read" survives a successful read on return`,
    async () => {
      if (route === 'CLICK') await bPickedAndRefused();
      else {
        await showingA();
        answers.recent![A] = 'refused';
        await reads(A, 1);
        await readOutcomes().first().waitFor({ timeout: 10_000 });
      }
      const id = route === 'CLICK' ? B : A;
      answers.recent![id] = route === 'CLICK' ? 'empty' : 'ok';
      await go('di-rail-home');
      await p().waitForSelector('[data-testid="di-scan-panel"][data-view="list"]');
      await go('di-rail-new-orchestrator');
      await p().waitForSelector(sel('di-new-mission'));
      // on return the old notice is not re-associated (it is from before this showing), and the watch reads the conversation again at once
      assert.equal(await readOutcomes().count(), 0, 'no old notice under the conversation on return');
      await reads(id, 1);
      assert.equal(await readOutcomes().count(), 0, 'still none after the successful read');
      if (route === 'CLICK') await p().waitForSelector(sel('di-new-thread-empty'));
      else assert.match(await p().locator(sel('di-new-thread')).innerText(), new RegExp(ASK_A));
      await ask().fill(TEXT);
      assert.equal(await sendHoldIsUnread(), false, 'no stale "not read" hold survives the round trip');
    },
    STEPS_MS,
  );

for (const route of ['CLICK', 'WATCH'] as const)
  test(
    `RECOVERY 4 (${route}): after a refused read, a DIFFERENT conversation is picked and read: the earlier notice is not said under it`,
    async () => {
      let other: string;
      if (route === 'CLICK') {
        await bPickedAndRefused();
        other = A;
      } else {
        await showingA();
        answers.recent![A] = 'refused';
        await reads(A, 1);
        await readOutcomes().first().waitFor({ timeout: 10_000 });
        other = B;
      }
      await recentAsk(other).click();
      await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, other === A ? ASK_A : ASK_B);
      assert.equal(await readOutcomes().count(), 0, 'the earlier conversation\'s notice is not attached to this one');
      assert.equal(await recentAsk(other).getAttribute('aria-current'), 'true');
      await ask().fill(TEXT);
      assert.equal(await sendHoldIsUnread(), false);
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

// ── Review round 2 (Lead1, the identity case): a read reply that lands LATE, about a conversation the person has already left. Attribution is by the request a
// reply answers (its seq, and the conversation it was about), never by when it arrived: a late failure of A is never said as a failure of B.

const threadWords = async () => ((await p().locator(sel('di-new-thread')).count()) > 0 ? await p().locator(sel('di-new-thread')).innerText() : '');
async function pickHeld(...ids: string[]): Promise<void> {
  for (const id of ids) holdReads.add(id);
  for (const id of ids) {
    await recentAsk(id).click();
    const end = Date.now() + 10_000;
    while (conversationReads(id) < 1 && Date.now() < end) await p().waitForTimeout(100);
  }
}
async function availableWithRecent(how: Record<string, 'ok' | 'empty' | 'refused' | 'unknown'>): Promise<void> {
  answers = { panel: 'available', turn: 'ok', recent: how };
  await boot();
  await panelBecomes('available');
}
for (const kind of ['refused', 'unknown'] as const) {
  test(
    `IDENTITY 1 (${kind.toUpperCase()}): A's read is held; B is picked (its read held too); A's ${kind} lands while B is shown and UNREAD: B is not marked failed`,
    async () => {
      await availableWithRecent({ [A]: kind, [B]: 'ok' });
      await pickHeld(A, B);
      await ask().fill(TEXT);
      assert.match((await send().getAttribute('title')) ?? '', /Reading this conversation/, 'precondition: B is current and being read');
      release(A);
      await p().waitForTimeout(800);
      assert.equal(await readOutcomes().count(), 0, `A's ${kind} is not said under B`);
      assert.match((await send().getAttribute('title')) ?? '', /Reading this conversation/, 'B is still being read, not "could not be read"');
      release(B);
      await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, ASK_B);
      assert.equal(await readOutcomes().count(), 0);
      assert.equal(await sendHoldIsUnread(), false);
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );
  test(
    `IDENTITY 2 (${kind.toUpperCase()}): A's read is held; B is picked and READ successfully; A's ${kind} lands after: B keeps saying it was read`,
    async () => {
      await availableWithRecent({ [A]: kind, [B]: 'ok' });
      await pickHeld(A);
      await recentAsk(B).click();
      await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, ASK_B);
      release(A);
      await p().waitForTimeout(800);
      assert.equal(await readOutcomes().count(), 0, `A's late ${kind} is not said under B`);
      assert.match(await threadWords(), new RegExp(ASK_B), 'B as read');
      await ask().fill(TEXT);
      assert.equal(await sendHoldIsUnread(), false);
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );
}
for (const [order, how] of [
  ['B answers first, then A', { [A]: 'ok', [B]: 'ok' }],
  ['A answers first, then B', { [A]: 'ok', [B]: 'ok' }],
  ['B (refused) first, then A (read)', { [A]: 'ok', [B]: 'refused' }],
] as const)
  test(
    `IDENTITY 4, overlapping reads in flight: A picked, then B picked, before either answers; ${order}: nothing is attributed to the wrong conversation`,
    async () => {
      await availableWithRecent({ ...how });
      await pickHeld(A, B);
      if (order.startsWith('A')) {
        release(A);
        await p().waitForTimeout(500);
        assert.doesNotMatch(await threadWords(), new RegExp(ASK_A), "A's late read is not drawn as the current conversation");
        release(B);
      } else {
        release(B);
        await p().waitForTimeout(500);
        release(A);
      }
      await p().waitForTimeout(800);
      assert.doesNotMatch(await threadWords(), new RegExp(ASK_A), 'A is never shown while B is current');
      if (how[B] === 'ok') {
        assert.match(await threadWords(), new RegExp(ASK_B), 'B as read');
        assert.equal(await readOutcomes().count(), 0);
      } else {
        // B's OWN refusal is B's: said under B, and nothing of A
        assert.equal(await readOutcomes().count(), 1);
        assert.match(await readOutcomes().first().innerText(), /^Not read\./);
        await ask().fill(TEXT);
        assert.equal(await sendHoldIsUnread(), true, 'B, not read, holds Send');
      }
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

// ── R3-REC (review round 3): the page names a way out of a conversation it could not read, so that way out must be ON the page then, and must keep the ask.

for (const kind of ['refused', 'unknown'] as const)
  test(
    `R3-REC (${kind.toUpperCase()}): a picked conversation's read is ${kind}; the page offers the way out it names; using it clears the conversation, keeps the typed ask, sends nothing; then Send sends one turn into a new conversation`,
    async () => {
      await showingA();
      answers.recent![B] = kind;
      await recentAsk(B).click();
      await readOutcomes().first().waitFor({ timeout: 10_000 });
      await ask().fill(TEXT);
      // the message names the control, and the control is there (the thread, and its own "Start a new conversation", is not drawn in this state)
      const held = (await send().getAttribute('title')) ?? '';
      const label = ((await p().locator(sel('di-new-leave-unread')).textContent()) ?? '').trim();
      assert.equal(label, 'Start a new conversation, keeping your ask');
      assert.ok(held.toLowerCase().includes(label.toLowerCase()), `Send's hold names the control: ${held}`);
      assert.equal(await p().locator(sel('di-new-new-conversation')).count(), 0, 'the thread control is not drawn here');
      // The pick's read stamps the watch cadence. A poll that was already due used to fire while
      // this click was still settling (CI's click wait is long) and the assertion below then saw
      // that read. Inside the cadence, the watch must not read again.
      const readsAfterPick = conversationReads(B);
      await p().waitForTimeout(2_000);
      assert.equal(conversationReads(B), readsAfterPick, 'the watch does not read again inside the cadence after the pick');
      const before = sentByPage.length;
      await p().locator(sel('di-new-leave-unread')).click();
      await p().waitForTimeout(400);
      assert.deepEqual(sentByPage.slice(before).filter((m) => m.type === 'diMissionRequest'), [], 'the reset sends nothing');
      assert.equal(await ask().inputValue(), TEXT, 'the typed ask is kept, exactly');
      assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, 'no conversation is current (its id is cleared)');
      assert.equal(await readOutcomes().count(), 0, 'nothing is said about a conversation no longer shown');
      assert.equal(await p().locator(sel('di-new-leave-unread')).count(), 0, 'the way out is gone once used');
      assert.equal(await send().getAttribute('aria-disabled'), null, 'Send is offered');
      await send().click();
      const end = Date.now() + 10_000;
      while (turns().length < 1 && Date.now() < end) await p().waitForTimeout(100);
      assert.equal(turns().length, 1, 'one turn');
      assert.equal(turns()[0].conversationId ?? null, null, 'into a NEW conversation (no conversation id)');
      assert.equal(turns()[0].text, TEXT, 'the ask as typed');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'R3-REC, the existing reset where it holds: with a conversation drawn, the thread\'s own "Start a new conversation" still clears the box (unchanged), and the keeping control is not drawn',
  async () => {
    await showingA();
    await ask().fill(TEXT);
    assert.equal(await p().locator(sel('di-new-leave-unread')).count(), 0, 'not offered while the conversation is shown');
    const before = sentByPage.length;
    await p().locator(sel('di-new-new-conversation')).click();
    await p().waitForTimeout(300);
    assert.equal(await ask().inputValue(), '', 'the existing reset clears the unsent ask, as it always has');
    assert.deepEqual(sentByPage.slice(before).filter((m) => m.type === 'diMissionRequest'), [], 'and sends nothing');
  },
  STEPS_MS,
);

// R3-REC focus (Lead1): the control unmounts in the update its own activation causes. Where focus ACTUALLY lands, measured per route; the kept ask is proven
// to be IN the box after the click settles (two dispatches: newConversation, then setComposer).
for (const route of ['pointer', 'keyboard Enter', 'keyboard Space'] as const)
  test(
    `R3-REC focus, ${route}: after the way out is used, the box holds the kept ask and focus is in it, never on the page body`,
    async () => {
      await showingA();
      answers.recent![B] = 'refused';
      await recentAsk(B).click();
      await readOutcomes().first().waitFor({ timeout: 10_000 });
      await ask().fill(TEXT);
      if (route === 'pointer') await p().locator(sel('di-new-leave-unread')).click();
      else {
        await ask().focus();
        for (let i = 0; i < 20 && (await focused()) !== 'di-new-leave-unread'; i++) await p().keyboard.press('Tab');
        assert.equal(await focused(), 'di-new-leave-unread', 'reached by Tab');
        await p().keyboard.press(route === 'keyboard Enter' ? 'Enter' : ' ');
      }
      await p().waitForSelector(sel('di-new-leave-unread'), { state: 'detached', timeout: 5_000 });
      await p().waitForTimeout(300);
      const at = await p().evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        return { testid: a?.getAttribute('data-testid') ?? null, tag: a?.tagName ?? null, body: a === null || a === document.body };
      });
      console.log(`[R3-REC focus] ${route}: ${JSON.stringify(at)}`);
      assert.equal(await ask().inputValue(), TEXT, 'the box CONTAINS the kept ask after the click settles');
      // measured on <body> before the fix (logs/95); now the box that keeps the ask
      assert.equal(at.testid, 'di-new-ask', `focus is in the box with the kept ask (it is on ${at.testid ?? at.tag})`);
    },
    STEPS_MS,
  );

test(
  'READ AGAIN is held while a read of that conversation is in flight: the reason is shown, a press sends nothing, the control is offered again once that read settles refused, and a different conversation is still read at once',
  async () => {
    await bPickedAndRefused();
    assert.equal(await readAgain().isDisabled(), false, 'precondition: the live control, not held');
    holdReads.add(B);
    const before = conversationReads(B);
    await readAgain().click();
    const end = Date.now() + 10_000;
    while (conversationReads(B) <= before && Date.now() < end) await p().waitForTimeout(50);
    assert.equal(conversationReads(B), before + 1, 'the press asks once');
    await p().waitForFunction(() => {
      const el = document.querySelector('[data-testid="di-new-read-again"]');
      return el instanceof HTMLButtonElement && el.disabled;
    });
    const reason = (await readAgain().getAttribute('title')) ?? '';
    assert.match(reason, /already being read/, reason);
    const described = await readAgain().getAttribute('aria-describedby');
    assert.ok(described, 'the reason is spoken');
    assert.equal(((await p().locator(`[id="${described}"]`).innerText()) ?? '').trim(), reason);
    const heldCount = conversationReads(B);
    // A disabled control does not activate. This is a real press, not a forced one, and it must not ask again.
    await assert.rejects(() => readAgain().click({ timeout: 800 }));
    assert.equal(conversationReads(B), heldCount, 'a press while that read is pending sends nothing');
    release(B);
    await p().waitForFunction(() => {
      const el = document.querySelector('[data-testid="di-new-read-again"]');
      return el instanceof HTMLButtonElement && !el.disabled;
    });
    assert.equal(await readAgain().isDisabled(), false, 'offered again once the refused read settles');
    holdReads.add(B);
    const beforeSecond = conversationReads(B);
    await readAgain().click();
    const endSecond = Date.now() + 10_000;
    while (conversationReads(B) <= beforeSecond && Date.now() < endSecond) await p().waitForTimeout(50);
    await p().waitForFunction(() => {
      const el = document.querySelector('[data-testid="di-new-read-again"]');
      return el instanceof HTMLButtonElement && el.disabled;
    });
    const readsA = conversationReads(A);
    holdReads.add(A);
    await recentAsk(A).click();
    const endA = Date.now() + 10_000;
    while (conversationReads(A) <= readsA && Date.now() < endA) await p().waitForTimeout(50);
    assert.equal(conversationReads(A), readsA + 1, 'a different conversation is read at once while this one is still pending');
  },
  STEPS_MS,
);

/** New Mission with Recent A and B. `awaiting` also lists that conversation in Reception as waiting for an answer. */
async function bootRecent(awaiting?: string): Promise<void> {
  answers = { panel: 'available', turn: 'ok', recent: { [A]: 'ok', [B]: 'ok' }, receptionAwaiting: awaiting };
  await boot();
  await panelBecomes('available');
}

async function untilReads(id: string, n: number): Promise<void> {
  const end = Date.now() + 10_000;
  while (conversationReads(id) < n && Date.now() < end) await p().waitForTimeout(50);
  assert.equal(conversationReads(id), n, `${n} read(s) of ${id === A ? 'A' : 'B'}`);
}

/** The conversation is the one shown: its Recent ask is current, and Close is drawn. */
async function shown(id: string): Promise<void> {
  await p().waitForFunction((conversationId) => {
    const ask = document.querySelector(`[data-testid="di-new-recent-ask"][data-conversation="${conversationId}"]`);
    return ask?.getAttribute('aria-current') === 'true' && document.querySelector('[data-testid="di-new-close-conversation"]') !== null;
  }, id);
}

async function closeShown(): Promise<void> {
  await p().locator(sel('di-new-close-conversation')).click();
  await p().waitForSelector(sel('di-new-close-conversation'), { state: 'detached', timeout: 5_000 });
}

test(
  'PICK AGAIN after Close, while that read is still pending: A is shown, the reply lands in it, and the read is not asked again',
  async () => {
    await bootRecent();
    holdReads.add(A);
    await recentAsk(A).click();
    await untilReads(A, 1);
    await shown(A);
    await closeShown();
    assert.equal(await recentAsk(A).getAttribute('aria-current'), null, 'Close left A unselected, with its read still pending');
    await recentAsk(A).click();
    await shown(A);
    await p().waitForTimeout(400);
    assert.equal(conversationReads(A), 1, 'picking A again attached to the read already in flight');
    release(A);
    await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, ASK_A);
    assert.equal(conversationReads(A), 1, 'the reply landed without a second ask');
  },
  STEPS_MS,
);

test(
  'PICK AGAIN after another conversation, while the first read is still pending: A is shown, the reply lands in it, and the read is not asked again',
  async () => {
    await bootRecent();
    holdReads.add(A);
    await recentAsk(A).click();
    await untilReads(A, 1);
    holdReads.add(B);
    await recentAsk(B).click();
    await untilReads(B, 1);
    await shown(B);
    assert.equal(conversationReads(A), 1, 'B was read at once and A was not asked again');
    await recentAsk(A).click();
    await shown(A);
    await p().waitForTimeout(400);
    assert.equal(conversationReads(A), 1, 'picking A again attached to the read already in flight');
    assert.equal(conversationReads(B), 1);
    release(A);
    await p().waitForFunction((words) => document.querySelector('[data-testid="di-new-thread"]')?.textContent?.includes(words) === true, ASK_A);
    assert.equal(conversationReads(A), 1, 'A’s reply landed in A');
    assert.doesNotMatch(await p().locator(sel('di-new-thread')).innerText(), new RegExp(escapeRegExp(ASK_B)), 'B is not shown as A');
  },
  STEPS_MS,
);

/** The operational office, where Reception's inbox opens the request panel on one conversation. */
async function bootOperational(): Promise<void> {
  answers = { panel: 'available', turn: 'ok', recent: { [A]: 'ok', [B]: 'ok' }, receptionAwaiting: A };
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url.replace('di-app-scan.html', 'di-app.html'), { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(`${sel('di-office-panel')}, ${sel('di-office-panel-collapsed')}`, { timeout: 20_000 });
  if ((await p().locator(sel('di-office-panel-collapsed')).count()) > 0) await p().locator(sel('di-office-panel-collapsed')).click();
  const before = ops().filter((op) => op === 'refresh').length;
  const end = Date.now() + 20_000;
  while (ops().filter((op) => op === 'refresh').length <= before && Date.now() < end) await p().waitForTimeout(50);
  assert.ok(ops().filter((op) => op === 'refresh').length > before, 'the office read the request panel');
}

async function openAFromReception(): Promise<void> {
  await p().locator(sel('di-open-reception')).click();
  await p().waitForSelector(sel('di-reception-inbox'), { timeout: 10_000 });
  const card = p().locator('[data-testid="di-reception-entry"][data-entry-kind="panel"]', { hasText: ASK_A });
  await card.locator(sel('di-reception-open-panel')).click();
  await p().waitForSelector(sel('di-mr'), { timeout: 10_000 });
}

test(
  'PICK AGAIN from Reception, while that read is still pending: the panel shows A, the reply lands in it, and the read is not asked again',
  async () => {
    await bootOperational();
    holdReads.add(A);
    await openAFromReception();
    await untilReads(A, 1);
    // Close leaves the read pending and the conversation unselected. Escape peels the inbox, then the conversation.
    await p().keyboard.press('Escape');
    await p().locator(sel('di-mr-composer')).click();
    await p().keyboard.press('Escape');
    await p().waitForTimeout(300);
    assert.equal(conversationReads(A), 1, 'Close did not ask again');
    await openAFromReception();
    await p().waitForTimeout(400);
    assert.equal(conversationReads(A), 1, 'Reception attached to the read already in flight');
    release(A);
    await p().waitForFunction(
      (words) => (document.querySelector('[data-testid="di-mr-log"]')?.textContent ?? '').includes(words),
      ASK_A,
    );
    assert.equal(conversationReads(A), 1, 'the reply landed without a second ask');
  },
  STEPS_MS,
);
