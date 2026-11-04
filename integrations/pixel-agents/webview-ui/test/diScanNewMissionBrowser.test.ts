/**
 * New Mission, ONE screen with ONE composer, through the actual App in a real browser (Free UI streamline, 2026-11-04, approved Mission mn-92b246d…, B1-B6 and C1/C2).
 *
 * THE TESTED REQUIREMENT (B1): the person types the ask ONCE, presses Send, and the drafted proposal is there ON THE SAME SCREEN: no navigation, no second box, no re-entry. The ask
 * crosses as ONE `turn` carrying exactly the typed text (byte for byte); the reply and its draft are drawn in place. Also (B3): nothing on the screen submits, approves or starts; the
 * draft's onward control only opens the request workspace's Ask on the SAME conversation, where Submit stays the person's own step. (B5) Recent asks appear only as read; (B6) at the
 * Orchestrator limit, an unknown counted as held, Send is refused with its reason and the limit notice works; (B4) New Orchestrator is the rail's entry, and the rail's foot holds only
 * Minimize and Settings.
 *
 * REPLACES test/diScanAskHandoffBrowser.test.ts, whose subject (handing the ask to a second composer) no longer exists. What it protected and where it is now: the text arrives in full
 * (here: in the turn, byte for byte); nothing is sent before the person's Send (here); Cmd Return does what the control does (here); the capacity refusal countercase on the unmodified
 * fixture (here). The two-draft conflict (refused, withdrawn when stale, no refusal without an attempt) has no subject now: there is one draft.
 *
 * The office state is the session-mismatch v2 fixture (real projection output), in its labelled available-capacity variation for the interaction tests (diAvailableCapacity.ts). Every
 * request-panel answer is LABELLED SYNTHETIC (test/diNewMissionSynthetic.ts, the panel's own wire shape): nothing is live, no Codex and no panel run.
 *
 * Captures: with DI_STREAMLINE_SHOTS=<absolute dir>, the walkthrough views are written there as PNGs with new-mission-manifest.json (each view, the fixture and its sha256, and that every
 * request-panel answer was synthetic). Without it, nothing is written.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an explicit message.
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

import type { DiOfficeState } from '../../core/src/messages.ts';
import { availableCapacityOffice, capacityUseOf, FIXTURE_USE } from './diAvailableCapacity.ts';
import {
  AUTHORITY_OPS,
  GATE_LABEL,
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_OTHER_CONVERSATION_ID,
  SYNTHETIC_PROPOSAL,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json';
const office = (JSON.parse(fs.readFileSync(path.join(root, FIXTURE), 'utf-8')) as { state: DiOfficeState }).state;
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env['DI_STREAMLINE_SHOTS'] ?? null;
if (SHOTS !== null && !path.isAbsolute(SHOTS)) throw new Error('DI_STREAMLINE_SHOTS must be an absolute directory');

/** A text that loses something visible if any part of the hop trims, collapses, normalises or truncates it. */
const TEXT = '  Fix the login timeout — keep sessions alive.\n\nSecond paragraph: ünïcödé ✓, two  spaces, a tab\there.  ';
const RECENT_WORDS = 'SYNTHETIC: fix the flaky readiness test';

/** How this file's stand-in panel answers (per test). */
interface Answers {
  /** Answer the overview read at all (false: it is never read, so recent asks are not read). */
  overview: boolean;
  /** The recent conversations the overview lists. */
  recent: Array<Record<string, unknown>>;
  /** Hold the draft back (the conversation still shows the turn in flight) until released. */
  holdDraft: boolean;
}
let answers: Answers = { overview: true, recent: [], holdDraft: false };
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
const shots: Array<Record<string, unknown>> = [];

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
        if (msg.type !== 'diMissionRequest') return;
        // every answer below is LABELLED SYNTHETIC, in the panel's own wire shape
        if (msg.op === 'refresh' && answers.overview) ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', syntheticOverview(answers.recent))));
        if (msg.op === 'turn') {
          sentTurn = String(msg.text);
          ws.send(JSON.stringify(relayReply(msg.seq, 'turn', { ok: true, turn: syntheticTurn(sentTurn) })));
        }
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          if (id === SYNTHETIC_OTHER_CONVERSATION_ID)
            ws.send(JSON.stringify(relayReply(msg.seq, 'conversation', syntheticConversation([{ ...syntheticDraftedTurn(RECENT_WORDS), conversation_id: id }], id))));
          else if (sentTurn !== null)
            ws.send(
              JSON.stringify(
                relayReply(msg.seq, 'conversation', syntheticConversation([answers.holdDraft ? syntheticTurn(sentTurn) : syntheticDraftedTurn(sentTurn)])),
              ),
            );
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
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
}, STEPS_MS);

afterAll(async () => {
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
  if (SHOTS !== null && shots.length > 0) {
    const bytes = fs.readFileSync(path.join(root, FIXTURE));
    fs.writeFileSync(
      path.join(SHOTS, 'new-mission-manifest.json'),
      `${JSON.stringify(
        {
          fixture: true,
          label:
            'FIXTURE office state + LABELLED SYNTHETIC request-panel replies (webview-ui/test/diScanNewMissionBrowser.test.ts). The real App mounted from source by the Vite dev server; NOT a live backend, NOT the production bundle; no request panel, Codex or Herdr ran.',
          office: { path: `webview-ui/${FIXTURE}`, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), variation: 'availableCapacityOffice (diAvailableCapacity.ts) unless the view says unmodified' },
          requestPanel: 'every answer SYNTHETIC: test/diNewMissionSynthetic.ts',
          shots,
        },
        null,
        2,
      )}\n`,
    );
  }
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (New Mission browser): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  sentTurn = null;
  answers = { overview: true, recent: [], holdDraft: false };
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const ops = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op));
const turnsSent = () => sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op === 'turn');
const authoritySent = () => ops().filter((op) => (AUTHORITY_OPS as readonly string[]).includes(op));
const stageView = () => p().locator('[data-testid="di-scan-stage"]').getAttribute('data-view');

async function snap(name: string, note: string, into?: string): Promise<void> {
  if (SHOTS === null) return;
  if (into) await p().locator(into).first().scrollIntoViewIfNeeded();
  await p().waitForTimeout(150);
  const file = `${name}.png`;
  await p().screenshot({ path: path.join(SHOTS, file) });
  shots.push({ file, note, viewport: p().viewportSize(), sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(SHOTS, file))).digest('hex') });
}

const available = availableCapacityOffice(office);
async function bootToNewMission(state: DiOfficeState = available): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
}
const ask = () => p().locator(sel('di-new-ask'));
const send = () => p().locator(sel('di-new-send'));

test(
  'B1, THE SINGLE-COMPOSER PATH: type the ask ONCE, press Send, and the drafted proposal is there ON THE SAME SCREEN; one turn crossed, carrying exactly the typed text; nothing else was sent',
  async () => {
    await bootToNewMission();
    await snap('new-mission-01-empty', 'New Mission on first paint: one Ask box; Send held with its reason (type an ask first).');
    assert.equal(await p().locator('[data-testid="di-new-mission"] textarea').count(), 1, 'ONE composer on the screen');
    assert.equal(await p().locator(sel('di-mr-composer')).count(), 0, 'the request workspace is not open');
    assert.deepEqual(authoritySent(), []);
    assert.equal(turnsSent().length, 0, 'typing nothing sends nothing');

    await ask().fill(TEXT);
    assert.equal(await send().getAttribute('aria-disabled'), null, 'Send is offered once there is an ask');
    assert.equal(turnsSent().length, 0, 'typing sends nothing');
    await snap('new-mission-02-typed', 'The ask typed once, in the one box. Nothing has been sent.');

    answers.holdDraft = true;
    await send().click();
    // exactly ONE turn, with exactly the typed text
    await p().waitForFunction(() => document.querySelector('[data-testid="di-new-thread"]') !== null);
    assert.equal(turnsSent().length, 1, 'one Send, one turn');
    assert.equal(turnsSent()[0]!.text, TEXT, 'the ask crossed byte for byte');
    // the same screen, the same page: no navigation, no workspace, nothing to type again
    assert.equal(await stageView(), 'new', 'still on New Mission');
    assert.equal(await p().locator(sel('di-mr-composer')).count(), 0, 'no workspace was opened');
    assert.equal(await ask().inputValue(), '', 'the box is empty: the ask is now in the conversation, not to be typed again');
    const you = await p().locator(`${sel('di-new-turn-1')} [data-who="you"]`).textContent();
    assert.ok((you ?? '').includes('Fix the login timeout — keep sessions alive.'), `the ask is shown as sent: ${you}`);
    // while it is answered, Send is held, keeps its focus, and says why
    await p().waitForSelector(sel('di-new-turn-state-1'));
    assert.equal(await send().getAttribute('aria-disabled'), 'true', 'Send is held while the last message is answered');
    assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-testid')), 'di-new-send', 'Send kept its focus');
    await snap('new-mission-03-sent-waiting', 'Sent: the ask is in the conversation in place; the assistant is answering; Send is held.', sel('di-new-thread'));

    answers.holdDraft = false;
    await p().waitForSelector(sel('di-new-draft'), { timeout: 20_000 });
    assert.equal(await stageView(), 'new', 'the draft is drawn on New Mission');
    const objective = await p().locator(sel('di-new-draft-proposal-objective')).textContent();
    assert.equal((objective ?? '').trim(), SYNTHETIC_PROPOSAL.objective, 'the proposal, exactly as drafted');
    assert.ok(await p().locator(sel('di-new-draft-proposal')).isVisible(), 'the proposal is on screen');
    assert.match((await p().locator(sel('di-new-draft-note')).textContent()) ?? '', /A draft, not yet a proposal of record\. Drafting approved nothing and started nothing\./);
    assert.equal(turnsSent().length, 1, 'still exactly one turn');
    assert.deepEqual(authoritySent(), [], `nothing submitted, decided, withdrawn, run or deleted: ${JSON.stringify(ops())}`);
    await snap('new-mission-04-drafted-in-place', 'The drafted proposal, in place on New Mission, said to be a draft, with the gates named.', sel('di-new-draft'));
  },
  STEPS_MS,
);

test(
  'B1: Cmd Return in the box does what Send does: one turn, the full text, no line break added',
  async () => {
    await bootToNewMission();
    await ask().fill(TEXT);
    await ask().focus();
    await p().keyboard.press('Meta+Enter');
    await p().waitForSelector(sel('di-new-draft'), { timeout: 20_000 });
    assert.equal(turnsSent().length, 1);
    assert.equal(turnsSent()[0]!.text, TEXT, 'the key added no line break');
  },
  STEPS_MS,
);

test(
  'B3, NO AUTHORITY ON THIS SCREEN: no control submits, approves or starts; the draft\'s one onward control opens the workspace\'s Ask on the SAME conversation, where Submit is the person\'s own step',
  async () => {
    await bootToNewMission();
    await ask().fill(TEXT);
    await send().click();
    await p().waitForSelector(sel('di-new-draft'), { timeout: 20_000 });
    const labels = await p()
      .locator('[data-testid="di-new-mission"] button')
      .evaluateAll((buttons) => buttons.map((b) => (b.textContent ?? '').trim()));
    for (const label of labels) assert.doesNotMatch(label, GATE_LABEL, label);
    const pageText = (await p().locator(sel('di-new-mission')).innerText()).replace(/\s+/g, ' ');
    assert.doesNotMatch(pageText, /\b(is|was|has been|have been) (approved|started|submitted|recorded)\b/i, 'no word claims a decision, a start or a record');

    await p().click(sel('di-new-draft-open'));
    await p().waitForSelector(sel('di-mr-composer'));
    assert.equal(await p().locator(sel('di-mr-tab-ask')).getAttribute('aria-selected'), 'true', 'the workspace opened on Ask');
    await p().waitForSelector(sel('di-mr-draft'));
    assert.ok(await p().locator(sel('di-mr-submit')).isVisible(), 'Submit is there, in the workspace, for the person to press');
    assert.deepEqual(authoritySent(), [], 'opening it submitted nothing');
    assert.equal(turnsSent().length, 1);
    await snap('new-mission-05-workspace-ask', 'The draft\'s onward step: the request workspace on Ask, the SAME conversation; Submit is there, unpressed.');
  },
  STEPS_MS,
);

test(
  'B5, RECENT ASKS ONLY AS READ: listed from the panel\'s overview by their first words, and one opens its conversation here (a read); unread, the page says so and draws none',
  async () => {
    answers.recent = [syntheticConversationSummary(SYNTHETIC_OTHER_CONVERSATION_ID, RECENT_WORDS), syntheticConversationSummary(SYNTHETIC_CONVERSATION_ID, '')];
    await bootToNewMission();
    const chip = p().locator(sel('di-new-recent-ask'));
    await chip.first().waitFor();
    assert.equal(await chip.count(), 1, 'a conversation whose words are not kept is not listed as an ask');
    assert.equal(((await chip.first().textContent()) ?? '').trim(), RECENT_WORDS);
    await snap('new-mission-06-recent-read', 'Recent asks as the request panel listed them (synthetic overview).', sel('di-new-recent'));
    await chip.first().click();
    await p().waitForSelector(sel('di-new-draft'), { timeout: 20_000 });
    assert.ok(sentByPage.some((m) => m.op === 'conversation' && m.conversationId === SYNTHETIC_OTHER_CONVERSATION_ID), 'the conversation was read');
    assert.equal(turnsSent().length, 0, 'nothing was sent');
    assert.deepEqual(authoritySent(), []);
    await page!.close();
    page = null;

    answers = { overview: false, recent: [], holdDraft: false };
    await bootToNewMission();
    await p().waitForTimeout(500);
    assert.equal(((await p().locator(sel('di-new-recent-not-read')).textContent()) ?? '').trim(), 'Recent asks are not read in this office.');
    assert.equal(await p().locator(sel('di-new-recent-ask')).count(), 0, 'nothing invented');
  },
  STEPS_MS,
);

test(
  'B6, CAPACITY REFUSED, the unmodified fixture: 2 in use and 14 that cannot be checked reach the limit; Send is refused with that reason, the limit notice opens Missions, and nothing is sent',
  async () => {
    assert.deepEqual(capacityUseOf(office), FIXTURE_USE, 'the fixture as the guard reads it');
    await bootToNewMission(office);
    await ask().fill(TEXT);
    const words = /15 of 15 Orchestrators are in use or cannot be checked \(2 in use, 14 cannot be checked\)\. Finish or stop one to start another\./;
    assert.equal(await send().getAttribute('aria-disabled'), 'true');
    assert.match((await send().getAttribute('title')) ?? '', words);
    const described = await send().getAttribute('aria-describedby');
    assert.match(((await p().locator(`[id="${described}"]`).textContent()) ?? '').trim(), words, 'its description gives the same reason');
    // a REAL click on the held control (force: Playwright would otherwise wait for it to be enabled): its handler must refuse it
    await send().click({ force: true });
    await ask().press('Meta+Enter');
    await p().waitForTimeout(300);
    assert.equal(turnsSent().length, 0, 'nothing was sent');
    assert.equal(await ask().inputValue(), TEXT, 'the ask is kept');
    assert.match((await p().locator(sel('di-new-limit')).textContent()) ?? '', /15 of 15 Orchestrators are in use or cannot be checked/);
    await snap('new-mission-07-limit', 'The capacity guard on the UNMODIFIED fixture: an unknown counted as held; Send refused with its reason.');
    await p().click(sel('di-new-open-missions'));
    assert.equal(await stageView(), 'missions', 'the limit notice opens Missions');
  },
  STEPS_MS,
);

test(
  'B4: New Orchestrator is the rail\'s entry and only there; the rail\'s foot holds only Minimize and Settings; no New Mission or New Orchestrator button elsewhere',
  async () => {
    await bootToNewMission();
    assert.equal(await p().locator(`${sel('di-rail')} ${sel('di-rail-new-orchestrator')}`).count(), 1);
    const foot = await p().locator('.di-rail-foot button').evaluateAll((buttons) => buttons.map((b) => b.getAttribute('data-testid')));
    assert.deepEqual(foot, ['di-office-panel-minimize', 'di-rail-settings']);
    const elsewhere = await p().evaluate(() =>
      [...document.querySelectorAll('button')].filter((b) => /New (Orchestrator|Mission)/.test(b.textContent ?? '') && b.closest('[data-testid="di-rail"]') === null).length,
    );
    assert.equal(elsewhere, 0);
  },
  STEPS_MS,
);
