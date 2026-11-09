/**
 * D1b: Escape peels ONE layer per press, the topmost first, as RENDERED in the real App composition (Chrome), beyond New Mission itself (D1a,
 * diScanNewMissionDismissBrowser.test.ts):
 *
 *  S  Settings (components/ui/Modal.tsx) owns its keys: drawn over a New Mission that shows a conversation, it is a modal dialog (#root inert, focus
 *     and Tab inside); Cmd N does not reach the page; Escape closes Settings ONLY, with focus back on the control that opened it (its x does the same);
 *     the next Escape closes the conversation, the next returns Home.
 *  W  the request workspace's own nested picked states, one at a time, innermost first: an armed Reject or Withdraw (focus back on the control that
 *     armed it), a revision (focus to the composer, which keeps its words), the picked request (back in the list, focus on its row), and only then the
 *     workspace itself. A picked request holding words typed for it is NOT put back by Escape (that would clear them): the workspace closes and keeps
 *     them, and reopening it shows them exactly.
 *
 * The office state is the session-mismatch v2 fixture (real projection output) in its labelled available-capacity variation. Every request-panel answer is
 * LABELLED SYNTHETIC (test/diNewMissionSynthetic.ts): a request awaiting a decision, its frozen offer, one recent conversation. A decision, a withdrawal or
 * a submission would be recorded and never answered; none may be sent here. Nothing is live.
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
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  AUTHORITY_OPS,
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_PENDING_REF,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOffer,
  syntheticOverviewWithRequests,
  syntheticRequest,
  syntheticRequestSummary,
} from './diNewMissionSynthetic.ts';
import { tokenScopeAudit } from './diTokenScope.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const A = SYNTHETIC_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A';
const REF = SYNTHETIC_PENDING_REF;
/** A SECOND request awaiting a decision (LABELLED SYNTHETIC: the pending request's own wire shape, with its own ref and Mission). */
const OTHER = `lr-${'7'.repeat(32)}`;
const OTHER_MISSION = `mn-${'7'.repeat(32)}`;
const asOther = (j: Record<string, unknown>) => ({ ...j, request_ref: OTHER, mission_id: OTHER_MISSION });
/** Set by the no-transfer test: the overview lists both requests. */
let twoDecidable = false;
const TYPED = '  An unsent ask ✓\nkept exactly.  ';

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
          const overview = syntheticOverviewWithRequests([REF], [syntheticConversationSummary(A, ASK_A)]);
          if (twoDecidable) (overview.requests as unknown[]).push({ ...asOther(syntheticRequestSummary(REF)), objective: 'SYNTHETIC second request (a test record): another proposal awaiting a decision' });
          send(relayReply(msg.seq, 'refresh', overview));
        }
        if (msg.op === 'request') send(relayReply(msg.seq, 'request', msg.requestRef === OTHER ? asOther(syntheticRequest(REF)) : syntheticRequest(String(msg.requestRef))));
        if (msg.op === 'offer') send(relayReply(msg.seq, 'offer', msg.requestRef === OTHER ? asOther(syntheticOffer(REF)) : syntheticOffer(String(msg.requestRef))));
        if (msg.op === 'conversation') send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(ASK_A)], String(msg.conversationId))));
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
    console.warn(`SKIPPED (Escape layers): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  twoDecidable = false;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const authority = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op)).filter((op) => (AUTHORITY_OPS as readonly string[]).includes(op));
const view = () => p().locator(sel('di-scan-panel')).getAttribute('data-view');
const focusNow = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? (a.getAttribute('role') === 'dialog' ? 'DIALOG' : a.tagName));
  });
const rootInert = () => p().evaluate(() => document.getElementById('root')?.hasAttribute('inert') ?? null);
const settings = () => p().getByRole('dialog', { name: 'Settings' });
const focusInSettings = () =>
  p().evaluate(() => {
    const dialog = [...document.querySelectorAll('[role="dialog"]')].find((d) => d.textContent?.includes('Settings'));
    return dialog !== undefined && dialog.contains(document.activeElement);
  });
/** Settings' tab stops, by the rule its Tab trap uses (diModal.ts `dialogTabStops`), and which of them has focus (-1: none of them). */
const SETTINGS_STOPS = `(() => {
  const dialog = [...document.querySelectorAll('[role="dialog"]')].find((d) => d.getAttribute('aria-labelledby') && document.getElementById(d.getAttribute('aria-labelledby'))?.textContent === 'Settings');
  if (!dialog) return [];
  const selector = 'button:not([disabled]), input:not([disabled]), summary, [href], select:not([disabled]), textarea:not([disabled]), [tabindex]';
  return [...dialog.querySelectorAll(selector)].filter((el) => el.tabIndex >= 0 && el.getAttribute('aria-hidden') !== 'true' && el.getClientRects().length > 0);
})()`;
const settingsStops = () => p().evaluate(`${SETTINGS_STOPS}.length`) as Promise<number>;
const settingsStopAt = () => p().evaluate(`${SETTINGS_STOPS}.indexOf(document.activeElement)`) as Promise<number>;
const workspaceOpen = async () => (await p().locator(sel('di-mr')).count()) > 0;
const pickedRow = () => p().locator(`[data-testid="di-mr-request-${REF}"]`);

/** `hooks`: Instant Detection installed and its notice not yet shown, so the hooks notice's entrance is drawn. `toNewMission` false: stay on Home (the world, and its controls, uncovered). */
async function boot(hooks = false, toNewMission = true): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(
    JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION, ...(hooks ? { hooksEnabled: true, hooksInfoShown: false } : {}) }),
  );
  if (hooks) socket!.send(JSON.stringify({ type: 'hooksStatus', providerId: 'claude', installed: true }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  if (!toNewMission) {
    await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
    return;
  }
  await p().click(sel('di-rail-new-orchestrator'));
  await p().locator('[data-testid="di-new-recent-ask"]').first().waitFor({ timeout: 20_000 });
}
/** New Mission showing conversation A (read from Recent), with an ask typed. */
async function newMissionShowingA(): Promise<void> {
  await boot();
  await p().click(`[data-testid="di-new-recent-ask"][data-conversation="${A}"]`);
  await p().waitForSelector(sel('di-new-thread'), { timeout: 10_000 });
  await p().locator(sel('di-new-ask')).fill(TYPED);
}
/** The workspace open on Requests (New Mission's Review requests), the request awaiting a decision picked and read, its offer read. */
async function requestPicked(): Promise<void> {
  await boot();
  await p().click(sel('di-new-open-workspace'));
  await pickedRow().waitFor({ timeout: 10_000 });
  await pickedRow().click();
  await p().waitForSelector(sel('di-mr-detail'), { timeout: 10_000 });
  await p().waitForSelector(sel('di-mr-word'), { timeout: 10_000 });
  await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-reject"]')?.getAttribute('aria-disabled') !== 'true', undefined, { timeout: 10_000 });
}
async function escape(): Promise<void> {
  await p().keyboard.press('Escape');
  await p().waitForTimeout(400);
}
const isPicked = async () => (await pickedRow().getAttribute('aria-current')) === 'true';

// ── S: Settings owns its keys ────────────────────────────────────────────────────────────────────────────────────────────────────────────

test(
  'S1: Settings over a New Mission that shows a conversation is a modal dialog that owns every key: Tab stays in it, Cmd N does not reach the page, the FIRST Escape closes Settings ONLY (focus back on the rail Settings); the next closes the conversation; the next returns Home',
  async () => {
    await newMissionShowingA();
    await p().click(sel('di-rail-settings'));
    await settings().waitFor({ timeout: 5_000 });
    assert.equal(await settings().getAttribute('aria-modal'), 'true');
    assert.equal(await rootInert(), true, 'the page under Settings is inert');
    assert.equal(await focusInSettings(), true, 'focus is inside Settings');
    // pre-review 2: Tab is walked to the LAST of Settings' tab stops and across that boundary, and Shift+Tab back across the FIRST, so removing the wrap fails
    const stops = await settingsStops();
    assert.ok(stops >= 2, `Settings has at least two tab stops (${stops})`);
    for (let i = 0; i < stops + 2 && (await settingsStopAt()) !== stops - 1; i++) {
      await p().keyboard.press('Tab');
      assert.equal(await focusInSettings(), true, `Tab press ${i + 1} stays inside Settings`);
    }
    assert.equal(await settingsStopAt(), stops - 1, 'sequential Tab reached the LAST tab stop of Settings');
    await p().keyboard.press('Tab');
    assert.equal(await settingsStopAt(), 0, 'Tab from the LAST tab stop wraps to the FIRST');
    await p().keyboard.press('Shift+Tab');
    assert.equal(await settingsStopAt(), stops - 1, 'Shift+Tab from the FIRST tab stop wraps to the LAST');
    await p().keyboard.press('Meta+n');
    await p().waitForTimeout(300);
    assert.equal(await settings().count(), 1, 'Cmd N did not close Settings');
    // layer 1: Settings
    await escape();
    assert.equal(await settings().count(), 0, 'the first Escape closed Settings');
    assert.equal(await rootInert(), false, 'nothing is inert once it closed');
    assert.equal(await view(), 'new', 'the page under it did not move');
    assert.equal(await p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${A}"]`).getAttribute('aria-current'), 'true', 'the conversation under it is still shown');
    assert.equal(await focusNow(), 'di-rail-settings', 'focus is back on the control that opened Settings');
    // layer 2: the conversation (D1a); layer 3: New Mission
    await escape();
    assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, 'the next Escape closed the conversation');
    assert.equal(await p().locator(sel('di-new-ask')).inputValue(), TYPED, 'the ask is kept');
    assert.equal(await focusNow(), 'di-new-ask');
    await escape();
    assert.equal(await view(), 'list', 'the next Escape returned Home');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

test(
  'S1b (pre-review 2): Cmd N under Settings is consumed where it WOULD visibly change the page: over Home it does not open New Mission; with Settings closed, the same key does',
  async () => {
    await boot(false, false);
    assert.equal(await view(), 'list', 'Home is the page under Settings');
    await p().click(sel('di-rail-settings'));
    await settings().waitFor({ timeout: 5_000 });
    await p().keyboard.press('Meta+n');
    await p().waitForTimeout(300);
    assert.equal(await settings().count(), 1, 'Cmd N did not close Settings');
    await escape();
    assert.equal(await settings().count(), 0, 'Escape closed Settings');
    assert.equal(await view(), 'list', 'Cmd N under Settings did NOT open New Mission: the page under it is still Home');
    // the positive control: the same key, with nothing in front, does change this page, so the assertion above can fail
    await p().keyboard.press('Meta+n');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'new', undefined, { timeout: 5_000 });
    assert.equal(await view(), 'new', 'with Settings closed, Cmd N opens New Mission');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

for (const how of ['its x button', 'a click outside it (the backdrop)'] as const)
  test(
    `S2: Settings closed by ${how}: focus goes back to the control that opened it, never the page body; nothing under it moved`,
    async () => {
      await newMissionShowingA();
      await p().click(sel('di-rail-settings'));
      await settings().waitFor({ timeout: 5_000 });
      // pre-review 5: the close control is named for what it does and for WHICH dialog it closes, never only "x"
      if (how === 'its x button') await settings().getByRole('button', { name: 'Close Settings', exact: true }).click();
      else await p().mouse.click(30, 450);
      await settings().waitFor({ state: 'detached', timeout: 5_000 });
      await p().waitForTimeout(200);
      assert.equal(await focusNow(), 'di-rail-settings');
      assert.equal(await rootInert(), false);
      assert.equal(await view(), 'new');
    },
    STEPS_MS,
  );

test(
  'S3: Settings opened from Home\'s Options menu: Escape closes it and focus goes back to THAT control (the opener is whichever control was used: Settings is an item of the Options menu, which is gone once it closes, so the control the person used is the Options trigger)',
  async () => {
    await boot();
    await p().click(sel('di-rail-home'));
    await p().waitForSelector(sel('di-scan-options'));
    await p().click(sel('di-scan-options'));
    await p().waitForSelector(sel('di-scan-settings'));
    await p().click(sel('di-scan-settings'));
    await settings().waitFor({ timeout: 5_000 });
    await escape();
    assert.equal(await settings().count(), 0);
    assert.equal(await focusNow(), 'di-scan-options');
    assert.equal(await view(), 'list');
  },
  STEPS_MS,
);

// ── M: EVERY caller of the shared modal (components/ui/Modal.tsx), now drawn outside #root ────────────────────────────────────────────

/** The scan face draws the world's controls (the version label and its update notice among them) behind its disclosure. */
async function worldControlsShown(): Promise<void> {
  if ((await p().locator('[data-testid="di-world-disclosure"][aria-expanded="false"]').count()) > 0) await p().click(sel('di-world-disclosure'));
}
const CALLERS = [
  { name: 'Settings', title: 'Settings', hooks: false, open: async () => p().click(sel('di-rail-settings')), back: 'di-rail-settings' },
  {
    name: 'the changelog, from the version label',
    title: /^What.s New in v/,
    hooks: false,
    open: async () => {
      await worldControlsShown();
      await p().getByRole('button', { name: /^v[\d.]+, see what.s new$/ }).click();
    },
    back: 'BUTTON',
  },
  {
    // the notice unmounts as it opens the changelog: its opener is gone, so the shared fallback takes focus
    name: 'the changelog, from the update notice',
    title: /^What.s New in v/,
    hooks: false,
    open: async () => {
      await worldControlsShown();
      await p().getByRole('button', { name: /^Updated to v/ }).click();
    },
    back: 'di-rail-home',
  },
  {
    name: 'paste import, from Settings',
    title: 'Import Layout (paste JSON)',
    hooks: false,
    open: async () => {
      await p().click(sel('di-rail-settings'));
      await settings().waitFor();
      await p().getByRole('button', { name: /Import Layout \(paste JSON\)/ }).click();
    },
    back: 'di-rail-settings',
  },
  {
    // its entrance is a link-styled text that cannot take focus: the shared fallback takes it
    name: 'the hooks notice',
    title: 'Instant Detection is ON',
    hooks: true,
    open: async () => {
      await p().getByText('Instant Detection Active').first().waitFor();
      await p().locator('span.cursor-pointer.underline').first().click();
    },
    back: 'di-rail-home',
  },
] as const;

for (const caller of CALLERS)
  test(
    `M: ${caller.name} is a labelled modal dialog drawn outside #root with its tokens resolved (the computed token-scope audit over EVERY rule finds no gap); Escape closes it alone and focus lands on ${caller.back}, never the page body`,
    async () => {
      await boot(caller.hooks, false);
      const under = await view();
      await caller.open();
      const dialog = p().getByRole('dialog', { name: caller.title });
      await dialog.waitFor({ timeout: 5_000 });
      assert.equal(await p().getByRole('dialog').count(), 1, 'one dialog in front');
      assert.equal(await dialog.getAttribute('aria-modal'), 'true');
      assert.equal(await dialog.evaluate((el) => document.getElementById('root')?.contains(el)), false, 'drawn outside #root');
      assert.equal(await rootInert(), true);
      assert.equal(await dialog.evaluate((el) => el.contains(document.activeElement)), true, 'focus inside');
      // pre-review 5: its close control is named "Close" plus THIS dialog's own title, and no control is named only "x"
      const closeName = typeof caller.title === 'string' ? `Close ${caller.title}` : new RegExp(`^Close ${caller.title.source.replace(/^\^/, '')}`);
      assert.equal(await dialog.getByRole('button', { name: closeName, exact: typeof closeName === 'string' }).count(), 1, `its close button is named for closing ${caller.name}`);
      assert.equal(await dialog.getByRole('button', { name: 'x', exact: true }).count(), 0, 'no control is named only "x"');
      // token scope after the portal move: the panel's own colours resolve, and the shared audit finds no consumed token without a value
      const surface = await dialog.evaluate((el) => {
        const cs = getComputedStyle(el);
        // the browser's own value for an unset colour, resolved here (no colour literal in the test)
        const probe = document.body.appendChild(document.createElement('div'));
        probe.style.backgroundColor = 'transparent';
        const transparent = getComputedStyle(probe).backgroundColor;
        probe.remove();
        return { background: cs.backgroundColor, border: cs.borderTopColor, font: cs.fontFamily, transparent };
      });
      assert.notEqual(surface.background, surface.transparent, `the panel is not transparent: ${JSON.stringify(surface)}`);
      assert.notEqual(surface.border, surface.transparent, `its border resolves: ${JSON.stringify(surface)}`);
      assert.match(surface.font, /FS Pixel Sans/, 'its font resolves');
      const audit = await tokenScopeAudit(p(), '');
      assert.deepEqual(audit.gaps, [], `token-scope gaps with ${caller.name} open`);
      assert.ok(audit.pairsChecked > 0, 'the audit checked this rendered state');
      await escape();
      assert.equal(await p().getByRole('dialog').count(), 0, 'Escape closed it');
      assert.equal(await rootInert(), false);
      const at = await focusNow();
      assert.notEqual(at, 'BODY', 'focus is not dropped to the page body');
      if (caller.back === 'BUTTON')
        assert.equal(await p().evaluate(() => /see what.s new/.test(document.activeElement?.getAttribute('aria-label') ?? '')), true, `focus is back on the version label (it is on ${at})`);
      else assert.equal(at, caller.back);
      assert.equal(await view(), under, 'the page under it did not move');
    },
    STEPS_MS,
  );

// ── W: the request workspace's nested picked states, one at a time ─────────────────────────────────────────────────────────────────────

test(
  'W1: a picked request with nothing typed for it: the FIRST Escape puts it back in the list (the workspace stays, focus on its row); the SECOND closes the workspace (focus on its opener)',
  async () => {
    await requestPicked();
    await p().locator(sel('di-mr-detail-heading')).focus();
    await escape();
    assert.equal(await workspaceOpen(), true, 'the workspace is still open');
    assert.equal(await isPicked(), false, 'the request is no longer picked');
    assert.equal(await p().locator(sel('di-mr-detail')).count(), 0, 'its detail is gone');
    assert.equal(await p().locator(sel('di-mr-nothing-selected')).count(), 1, 'the list says to choose a request');
    assert.equal(await focusNow(), `di-mr-request-${REF}`, 'focus is on its row (its detail, which had focus, unmounted)');
    await escape();
    assert.equal(await workspaceOpen(), false, 'the second Escape closed the workspace');
    assert.equal(await focusNow(), 'di-new-open-workspace');
    assert.deepEqual(authority(), []);
  },
  STEPS_MS,
);

test(
  'W2: a picked request HOLDING words typed for it (the decision word and a change request\'s reason): the FIRST Escape still puts it back (the same grammar), the words are shown for no request while none is picked, and picking it again gives them back exactly; then Escape puts it back, then closes',
  async () => {
    await requestPicked();
    await p().locator(sel('di-mr-word')).fill('approve');
    await p().locator(sel('di-mr-change-reason')).fill('  SYNTHETIC reason: the scope is too wide ✓  ');
    await p().locator(sel('di-mr-word')).focus();
    await escape();
    assert.equal(await workspaceOpen(), true, 'the workspace is still open');
    assert.equal(await isPicked(), false, 'the first Escape put the request back');
    assert.equal(await focusNow(), `di-mr-request-${REF}`, 'focus is on its row');
    const shownWords = await p().evaluate(() => [...document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-testid="di-mr"] input, [data-testid="di-mr"] textarea')].map((el) => el.value).filter((v) => v.includes('approve') || v.includes('SYNTHETIC reason')));
    assert.deepEqual(shownWords, [], 'the words are not shown anywhere while no request is picked');
    await pickedRow().click();
    await p().waitForSelector(sel('di-mr-word'), { timeout: 10_000 });
    assert.equal(await p().locator(sel('di-mr-word')).inputValue(), 'approve', 'the word, exactly, with its own request');
    assert.equal(await p().locator(sel('di-mr-change-reason')).inputValue(), '  SYNTHETIC reason: the scope is too wide ✓  ', 'the reason, exactly');
    await p().locator(sel('di-mr-detail-heading')).focus();
    await escape();
    assert.equal(await isPicked(), false);
    await escape();
    assert.equal(await workspaceOpen(), false, 'then Escape closes the workspace');
    assert.deepEqual(authority(), [], 'nothing was decided');
  },
  STEPS_MS,
);

for (const route of ['the draft\'s "Open it in the request workspace"', 'Review requests, then Ask'] as const)
  test(
    `W6 (1a): the workspace on Ask showing a PICKED conversation (opened by ${route}): the FIRST Escape closes that conversation in the workspace (the composer keeps its words, focus in it, the workspace stays open); the SECOND closes the workspace; New Mission is then blank with the same words, and the next Escape returns Home`,
    async () => {
      await boot();
      await p().click(`[data-testid="di-new-recent-ask"][data-conversation="${A}"]`);
      await p().waitForSelector(sel('di-new-thread'), { timeout: 10_000 });
      if (route.startsWith('the draft')) await p().click(sel('di-new-draft-open'));
      else {
        await p().click(sel('di-new-open-workspace'));
        await p().click(sel('di-mr-tab-ask'));
      }
      await p().waitForSelector(sel('di-mr-composer'));
      await p().waitForSelector(sel('di-mr-turn-1'), { timeout: 10_000 });
      await p().locator(sel('di-mr-composer')).fill(TYPED);
      await p().locator(sel('di-mr-heading')).focus();
      // layer 1: the conversation picked inside the workspace
      await escape();
      assert.equal(await workspaceOpen(), true, 'the workspace is still open');
      assert.equal(await p().locator(sel('di-mr-turn-1')).count(), 0, 'the conversation is closed in the workspace');
      assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), TYPED, 'the composer keeps its words');
      assert.equal(await focusNow(), 'di-mr-composer', 'focus is in the composer');
      // layer 2: the workspace
      await escape();
      assert.equal(await workspaceOpen(), false, 'the second Escape closed the workspace');
      assert.notEqual(await focusNow(), 'BODY', 'focus is not dropped to the page body');
      assert.equal(await view(), 'new');
      assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, 'New Mission is blank: the one conversation was closed');
      assert.equal(await p().locator(sel('di-new-ask')).inputValue(), TYPED, 'the ONE draft is New Mission\'s box, kept');
      // layer 3: New Mission
      await p().locator(sel('di-new-ask')).focus();
      await escape();
      assert.equal(await view(), 'list');
      assert.deepEqual(authority(), []);
    },
    STEPS_MS,
  );

test(
  'W3: Reject armed: Escape disarms it (focus back on "Reject for good…"), its "Keep it" does the same; then Escape puts the request back; then closes. Nothing is rejected',
  async () => {
    await requestPicked();
    await p().locator(sel('di-mr-reject')).click();
    await p().waitForSelector(sel('di-mr-reject-confirming'));
    await escape();
    assert.equal(await p().locator(sel('di-mr-reject-confirming')).count(), 0, 'Escape disarmed Reject');
    assert.equal(await workspaceOpen(), true);
    assert.equal(await isPicked(), true, 'the request is still picked');
    assert.equal(await focusNow(), 'di-mr-reject', 'focus is back on the control that armed it');
    await p().locator(sel('di-mr-reject')).click();
    await p().waitForSelector(sel('di-mr-reject-confirming'));
    await p().locator(sel('di-mr-reject-cancel')).click();
    await p().waitForSelector(sel('di-mr-reject-confirming'), { state: 'detached' });
    await p().waitForTimeout(200);
    assert.equal(await focusNow(), 'di-mr-reject', '"Keep it" hands focus back to "Reject for good…", never to the page or the workspace heading');
    await escape();
    assert.equal(await isPicked(), false, 'then Escape puts the request back');
    await escape();
    assert.equal(await workspaceOpen(), false, 'then Escape closes the workspace');
    assert.deepEqual(authority(), [], 'nothing was rejected');
  },
  STEPS_MS,
);

test(
  'W4: Withdraw armed: Escape disarms it (focus back on Withdraw, as before); then Escape puts the request back; then closes. Nothing is withdrawn',
  async () => {
    await requestPicked();
    await p().locator(sel('di-mr-withdraw')).click();
    await p().waitForSelector(sel('di-mr-withdraw-confirm-box'));
    await escape();
    assert.equal(await p().locator(sel('di-mr-withdraw-confirm-box')).count(), 0);
    assert.equal(await focusNow(), 'di-mr-withdraw');
    assert.equal(await isPicked(), true);
    await escape();
    assert.equal(await isPicked(), false);
    assert.equal(await focusNow(), `di-mr-request-${REF}`);
    await escape();
    assert.equal(await workspaceOpen(), false);
    assert.deepEqual(authority(), [], 'nothing was withdrawn');
  },
  STEPS_MS,
);

test(
  'W5: revising a request: Escape stops the revision (that proposal is kept, the composer keeps its words, focus in it); the next closes the workspace. "Stop revising" puts focus in the composer too. Nothing is submitted',
  async () => {
    await requestPicked();
    await p().locator(sel('di-mr-revise')).click();
    await p().waitForSelector(sel('di-mr-revising'));
    await p().locator(sel('di-mr-composer')).fill(TYPED);
    await escape();
    assert.equal(await p().locator(sel('di-mr-revising')).count(), 0, 'Escape stopped the revision');
    assert.equal(await workspaceOpen(), true, 'the workspace is still open');
    assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), TYPED, 'the composer keeps its words');
    assert.equal(await focusNow(), 'di-mr-composer');
    // the box's own control, the same step
    await p().click(sel('di-mr-tab-requests'));
    await p().locator(sel('di-mr-revise')).click();
    await p().waitForSelector(sel('di-mr-revising'));
    await p().locator(sel('di-mr-cancel-revision')).click();
    await p().waitForSelector(sel('di-mr-revising'), { state: 'detached' });
    await p().waitForTimeout(200);
    assert.equal(await focusNow(), 'di-mr-composer', '"Stop revising" puts focus in the composer, never the page');
    await escape();
    assert.equal(await workspaceOpen(), false, 'with nothing left to peel, Escape closes the workspace');
    assert.equal(await p().locator(sel('di-new-ask')).inputValue(), TYPED, 'the ONE draft is New Mission\'s box, kept');
    assert.deepEqual(authority(), [], 'nothing was submitted');
  },
  STEPS_MS,
);

test(
  'W7 (addendum 3, NO TRANSFER): the decision word and change reason typed for request X never show on request Y and cannot be sent against it; picking X again gives them back exactly, and the only decision sent names X',
  async () => {
    twoDecidable = true;
    await requestPicked();
    await p().locator(sel('di-mr-word')).fill('approve');
    await p().locator(sel('di-mr-change-reason')).fill('SYNTHETIC reason typed for X');
    // pick Y
    await p().locator(`[data-testid="di-mr-request-${OTHER}"]`).click();
    await p().waitForFunction((other) => document.querySelector(`[data-testid="di-mr-request-${other}"]`)?.getAttribute('aria-current') === 'true', OTHER);
    await p().waitForSelector(sel('di-mr-word'), { timeout: 10_000 });
    await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-reject"]')?.getAttribute('aria-disabled') !== 'true', undefined, { timeout: 10_000 });
    assert.equal(await p().locator(sel('di-mr-word')).inputValue(), '', "Y's decision word box is empty");
    assert.equal(await p().locator(sel('di-mr-change-reason')).inputValue(), '', "Y's change reason is empty");
    const shown = await p().evaluate(() => [...document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-testid="di-mr"] input, [data-testid="di-mr"] textarea')].map((el) => el.value).filter((v) => v.includes('approve') || v.includes('reason typed for X')));
    assert.deepEqual(shown, [], "nothing typed for X is shown anywhere while Y is picked");
    // try to act on Y with nothing typed for it: Approve and Ask for changes are held and send nothing
    const before = sentByPage.length;
    assert.equal(await p().locator(sel('di-mr-approve')).getAttribute('aria-disabled'), 'true', 'Approve on Y is held');
    await p().locator(sel('di-mr-approve')).click({ force: true });
    assert.equal(await p().locator(sel('di-mr-request-changes')).getAttribute('aria-disabled'), 'true', 'Ask for changes on Y is held (no reason typed for Y)');
    await p().locator(sel('di-mr-request-changes')).click({ force: true });
    await p().waitForTimeout(500);
    assert.deepEqual(sentByPage.slice(before).filter((m) => m.type === 'diMissionRequest' && (AUTHORITY_OPS as readonly string[]).includes(String(m.op))), [], 'nothing was sent against Y');
    // back to X: its own words, exactly
    await pickedRow().click();
    await p().waitForFunction((ref) => document.querySelector(`[data-testid="di-mr-request-${ref}"]`)?.getAttribute('aria-current') === 'true', REF);
    await p().waitForSelector(sel('di-mr-word'), { timeout: 10_000 });
    assert.equal(await p().locator(sel('di-mr-word')).inputValue(), 'approve', "X's word, exactly");
    assert.equal(await p().locator(sel('di-mr-change-reason')).inputValue(), 'SYNTHETIC reason typed for X', "X's reason, exactly");
    // the word is used for X only: the one decision sent names X (synthetic; recorded and never answered)
    await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-approve"]')?.getAttribute('aria-disabled') !== 'true', undefined, { timeout: 10_000 });
    await p().locator(sel('di-mr-approve')).click();
    const end = Date.now() + 5_000;
    while (!sentByPage.some((m) => m.op === 'decide') && Date.now() < end) await p().waitForTimeout(100);
    const decisions = sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op === 'decide');
    assert.equal(decisions.length, 1, 'one decision was sent');
    assert.equal(decisions[0].requestRef, REF, 'it names X');
    assert.equal(decisions.some((m) => m.requestRef === OTHER), false, 'no decision names Y');
  },
  STEPS_MS,
);

// ── B1: the front stack's split, seen by the scan face's Escape ladder and the world Map's key guard ─────────────────────────────────────
// SYNTHETIC STATE INJECTION, labelled: B1 has no popover yet, so a NON-MODAL claim is placed directly through the APP'S OWN diModalStack module (the page
// imports the same URL the app does, so it is the same module instance; the positive control after each release would fail otherwise). This proves the
// OWNERSHIP BOOKKEEPING only (the ladder and the Map's guard stand down while a non-modal claim is in front). A rendered trigger, a real menu, a real pointer
// landing elsewhere, real event propagation and real focus are B2's, not proven here.
/** The URL the app's own modules use for the stack under this harness's Vite root. The page code is a STRING, so vitest's transform never rewrites its import. */
const STACK_URL = '/src/components/diModalStack.ts';
const claimNonModal = () =>
  p().evaluate(
    `(async () => { const stack = await import('${STACK_URL}'); const owner = { b1: 'injected non-modal claim' }; window.__b1Owner = owner; stack.claimFront(owner, { modal: false }); return { front: stack.frontCount(), modal: stack.modalFrontCount(), inert: document.getElementById('root')?.hasAttribute('inert') ?? null }; })()`,
  ) as Promise<{ front: number; modal: number; inert: boolean | null }>;
const releaseNonModal = () =>
  p().evaluate(`(async () => (await import('${STACK_URL}')).releaseFront(window.__b1Owner))()`) as Promise<boolean>;

test(
  'B1 (SYNTHETIC injection): with a NON-MODAL claim in front, Escape does not peel the scan face (New Mission stays, its conversation stays) and nothing is inert; released, the same Escape peels again',
  async () => {
    await newMissionShowingA();
    assert.deepEqual(await claimNonModal(), { front: 1, modal: 0, inert: false }, 'a non-modal claim is in front, and it inerts nothing');
    await escape();
    assert.equal(await view(), 'new', 'the ladder stood down: still New Mission');
    assert.equal(await p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${A}"]`).getAttribute('aria-current'), 'true', 'the conversation was not closed');
    assert.equal(await releaseNonModal(), true, 'the injected claim was foremost when released');
    // the positive control: the same key, with nothing in front, peels exactly one layer (the conversation), so the assertions above can fail
    await escape();
    assert.equal(await p().locator('[data-testid="di-new-recent-ask"][aria-current="true"]').count(), 0, 'released: the first Escape closed the conversation');
    assert.equal(await view(), 'new');
  },
  STEPS_MS,
);
