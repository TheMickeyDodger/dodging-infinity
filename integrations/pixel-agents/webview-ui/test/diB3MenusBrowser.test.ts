/**
 * B3, 1B: THE OFFICE AND REQUEST ANCHORED POPOVERS (NOT a canonical test while it is in the evidence tree; PREPARED, NOT RUN). Records with the prefix B3MO.
 *
 * THE CLAIM, per surface and per viewport, each in its own test: the surface's secondary actions are in ONE anchored, non-modal popover (built on AnchoredPopover, the mechanism Mission details' More actions
 * proved), and the three-way dismissal contract holds: Escape or Close returns focus to the trigger; an OUTSIDE pointer must NOT steal focus from what the person chose; an item that opens a screen puts
 * focus where that screen's close will return it. `#root` is NOT inert while the popover is open; the closing Escape is CONSUMED (the screen under it is kept).
 *   Request workspace: "Options" holds the reading width (an action: the label flips to name what it does, no aria-pressed) and Minimize; Close requests stays DIRECT. Minimize leaves NO menu open under the hidden surface, and
 *   Restore puts focus on the last CONTENT control the person used, BY IDENTITY (the composer), never on a vanished menu item and never on the page.
 *   Office (Home header): "Options" holds Settings and the Needs you only filter (the item says what it filters, "Needs you only", and its trigger says "Options (needs you only)" while the filter is on). Settings, closed with Escape, returns focus to the
 *   Options trigger (a CHANGED destination: the existing pin expected the old icon button).
 * HONEST BOUNDS: Chrome, not Brave; synthetic conversation; the keyboard-route lengths of the moved controls are RECORDED, not asserted (a menu adds a step; the new count is owed as evidence);
 * the Office Escape-consumed claim needs the screen BESIDE the list, so it runs at the two shipping sizes only.
 * NEGATIVE CONTROL (on the CURRENT bytes): every test here fails at its FIRST claim line (the options trigger does not exist), after its STATE lines pass.
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
const REVIEW = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3MO_SHOTS ?? null;
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
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview(LONG.map((c) => syntheticConversationSummary(c.id, c.ask)))));
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
    console.warn(`SKIPPED (B3 menus): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  turnsOf = {};
  sent = [];
  refuse = new Set();
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const chipFor = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B3MO ${name}: ${JSON.stringify(r)}`);

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

/** The office with no screen open (Home), the same page as boot() without the rail click. */
async function bootHome(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-office-panel'));
}

// ── B3 1B: the Office and Request popovers ──────────────────────────────────────────────────────────────────────────────────────────────────────────
const ASK = 'SYNTHETIC an ask typed before the request workspace opens';
const WORDS = 'SYNTHETIC words that must come back byte for byte,  two  spaces, and a tab\there.';
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const TWO_PANE = SIZES.filter((v) => v.width >= 1440);
type Vp = (typeof SIZES)[number];

/** Who has focus, by identity: its testid (BODY when nothing does). */
const focusKey = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName.toLowerCase());
  });
const rootInert = () =>
  p().evaluate(() => {
    const r = document.getElementById('root');
    return r === null ? null : r.inert;
  });
const settingsDialog = () => p().getByRole('dialog', { name: 'Settings' });

/** Open the request workspace on its Ask with WORDS typed and focus left in the composer (the tray input's route). */
async function openRequest(vp: Vp): Promise<void> {
  await boot(vp);
  await p().locator(sel('di-new-ask')).fill(ASK);
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
  await p().click(sel('di-mr-tab-ask'));
  await p().waitForSelector(sel('di-mr-composer'), { timeout: 10_000 });
  await p().locator(sel('di-mr-composer')).fill(WORDS);
  await p().locator(sel('di-mr-composer')).focus();
  assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'STATE: the request popover starts closed');
  assert.equal(await p().locator(sel('di-mr-close')).count(), 1, 'STATE: Close requests is drawn');
}
for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 MENUS, request workspace, at ${where}, OPEN: a labelled trigger, a non-modal popover, focus on its first item`, async () => {
    await openRequest(vp);
    const trigger = p().locator(sel('di-mr-options'));
    assert.equal(await trigger.getAttribute('aria-haspopup'), 'dialog', 'CONTRACT (OPEN): the trigger announces a popup');
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false', 'CONTRACT (OPEN): the trigger starts collapsed');
    assert.ok((await rootInert()) !== null, 'STATE: #root exists');
    await trigger.click();
    await p().waitForSelector(sel('di-mr-options-popover'));
    record(`REQ-OPEN ${vp.width}x${vp.height}`, { focus: await focusKey(), items: await p().locator(`${sel('di-mr-options-popover')} [data-testid^="di-mr-"]`).evaluateAll((els) => els.map((e) => e.getAttribute('data-testid'))) });
    assert.equal(await trigger.getAttribute('aria-expanded'), 'true', 'CONTRACT (OPEN): expanded while open');
    assert.equal(await rootInert(), false, 'CONTRACT (OPEN): a non-modal popover leaves #root live');
    assert.equal(await focusKey(), 'di-mr-wide', 'CONTRACT (OPEN): focus is on the first item');
    assert.equal(await p().locator(sel('di-mr-minimize')).count(), 1, 'CONTRACT (OPEN): Minimize is in the popover');
  });
  test(`B3 MENUS, request workspace, at ${where}, DISMISS: Escape returns to the trigger and is consumed; an outside pointer does not steal focus`, async () => {
    await openRequest(vp);
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel('di-mr-options-popover'));
    await p().keyboard.press('Escape');
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (ESCAPE): the popover closed');
    assert.equal(await p().locator('.di-mr:not([hidden])').count(), 1, 'CONTRACT (ESCAPE): the workspace is KEPT (the press was consumed)');
    assert.equal(await focusKey(), 'di-mr-options', 'CONTRACT (ESCAPE): focus is on the trigger');
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel('di-mr-options-popover'));
    await p().locator(sel('di-mr-composer')).click();
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (OUTSIDE): the popover closed');
    assert.equal(await focusKey(), 'di-mr-composer', 'CONTRACT (OUTSIDE): focus stays where the person put it');
  });
  test(`B3 MENUS, request workspace, at ${where}, WIDTH: an action named for what it does, that returns to the trigger`, async () => {
    await openRequest(vp);
    const before = await p().locator('.di-mr').getAttribute('data-wide');
    await p().focus(sel('di-mr-options'));
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-mr-options-popover'));
    assert.equal(await focusKey(), 'di-mr-wide', 'STATE: the toggle item holds focus');
    await p().keyboard.press('Enter');
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (WIDTH): selecting closes the popover');
    assert.equal(await focusKey(), 'di-mr-options', 'CONTRACT (WIDTH): focus is on the trigger');
    const after = await p().locator('.di-mr').getAttribute('data-wide');
    assert.notEqual(after, before, 'CONTRACT (WIDTH): the reading width changed');
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel('di-mr-options-popover'));
    const widePressedAttr = await p().locator(sel('di-mr-wide')).getAttribute('aria-pressed');
    const wideLabel = (await p().locator(sel('di-mr-wide')).textContent())?.trim();
    record(`REQ-WIDTH ${vp.width}x${vp.height}`, { before, after, pressed: widePressedAttr, label: wideLabel });
    assert.equal(widePressedAttr, null, 'CONTRACT (WIDTH): the item is an ACTION named for what it does, so it carries no aria-pressed (a pressed "Narrower reading" would say the opposite of the action)');
    assert.equal(wideLabel, after === 'true' ? 'Narrower reading' : 'Wider reading', 'CONTRACT (WIDTH): the label names the action that undoes the state it holds');
  });
  test(`B3 MENUS, request workspace, at ${where}, MINIMIZE: no menu is left open and Restore returns to the last content control, by identity`, async () => {
    await openRequest(vp);
    await p().focus(sel('di-mr-options'));
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('di-mr-options-popover'));
    await p().keyboard.press('Tab');
    assert.equal(await focusKey(), 'di-mr-minimize', 'STATE: the next stop after the width toggle is Minimize');
    await p().keyboard.press('Enter');
    await p().waitForSelector('.di-mr[hidden]', { state: 'attached' });
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (MINIMIZE): no popover is left open under the hidden surface');
    await shot(`b3mo-request-minimized-${vp.width}x${vp.height}.png`);
    record(`REQ-MINIMIZED ${vp.width}x${vp.height}`, { focus: await focusKey() });
    await p().click(sel('di-mr-restore'));
    await p().waitForSelector('.di-mr:not([hidden])', { timeout: 10_000 });
    record(`REQ-RESTORED ${vp.width}x${vp.height}`, { focus: await focusKey() });
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (RESTORE): the popover is not open');
    assert.equal(await focusKey(), 'di-mr-composer', 'CONTRACT (RESTORE): focus returns to the control last used, by identity, not to a vanished menu item or the page');
    assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), WORDS, 'CONTRACT (RESTORE): the typed words are retained byte for byte');
  });
  test(`B3 MENUS, request workspace, at ${where}, MINIMIZE FALLBACK: when the control last used cannot take focus, Restore goes to the PERSISTENT Options trigger (the fallback), by identity, and the menu is not what is restored`, async () => {
    await openRequest(vp);
    // CONTROLLED condition (a composer disabled while a send is pending is a real state): the control last used is the composer, and it cannot take focus when the workspace comes back
    await p().focus(sel('di-mr-composer'));
    await p().evaluate(() => {
      (document.querySelector('[data-testid="di-mr-composer"]') as HTMLTextAreaElement).disabled = true;
    });
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel('di-mr-options-popover'));
    await p().click(sel('di-mr-minimize'));
    await p().waitForSelector('.di-mr[hidden]', { state: 'attached' });
    await p().click(sel('di-mr-restore'));
    await p().waitForSelector('.di-mr:not([hidden])', { timeout: 10_000 });
    record(`REQ-RESTORED-FALLBACK ${vp.width}x${vp.height}`, { focus: await focusKey() });
    assert.equal(await p().locator(sel('di-mr-options-popover')).count(), 0, 'CONTRACT (FALLBACK): no menu is restored open');
    assert.equal(await focusKey(), 'di-mr-options', 'CONTRACT (FALLBACK): with the last content control unable to take focus, focus lands on the persistent Options trigger (not on the page, not on a vanished menu item, not on the heading)');
  });
  test(`B3 MENUS, office Home, at ${where}, OPEN and FILTER: the secondaries are in one popover; the filter keeps its cue`, async () => {
    await bootHome(vp);
    assert.equal(await p().locator(sel('di-scan-filter')).count(), 0, 'STATE: the secondaries are not drawn until the popover opens');
    const trigger = p().locator(sel('di-scan-options'));
    assert.equal((await trigger.textContent())?.trim(), 'Options', 'CONTRACT (OPEN): the trigger says what it holds');
    await trigger.click();
    await p().waitForSelector(sel('di-scan-options-popover'));
    assert.equal(await rootInert(), false, 'CONTRACT (OPEN): a non-modal popover leaves #root live');
    record(`OFFICE-OPEN ${vp.width}x${vp.height}`, { focus: await focusKey(), settingsHeld: await p().locator(sel('di-scan-settings')).getAttribute('aria-disabled'), filterPressed: await p().locator(sel('di-scan-filter')).getAttribute('aria-pressed') });
    assert.equal(await p().locator(sel('di-scan-filter')).getAttribute('aria-pressed'), 'false', 'CONTRACT (FILTER): the toggle starts off');
    await p().locator(sel('di-scan-filter')).click();
    assert.equal(await p().locator(sel('di-scan-options-popover')).count(), 0, 'CONTRACT (FILTER): selecting closes the popover');
    assert.equal(await focusKey(), 'di-scan-options', 'CONTRACT (FILTER): focus is on the trigger');
    assert.equal((await trigger.textContent())?.trim(), 'Options (needs you only)', 'CONTRACT (FILTER): the filter is still announced once the popover is closed');
    await trigger.click();
    await p().waitForSelector(sel('di-scan-options-popover'));
    assert.equal(await p().locator(sel('di-scan-filter')).getAttribute('aria-pressed'), 'true', 'CONTRACT (FILTER): the toggle announces it is on');
  });
  test(`B3 MENUS, office Home, at ${where}, SETTINGS: Escape closes the dialog and focus goes back to the trigger that opened it`, async () => {
    await bootHome(vp);
    await p().click(sel('di-scan-options'));
    await p().waitForSelector(sel('di-scan-options-popover'));
    assert.equal(await p().locator(sel('di-scan-settings')).getAttribute('aria-disabled'), null, 'STATE: Settings is available in this office');
    await p().locator(sel('di-scan-settings')).click();
    await settingsDialog().waitFor({ timeout: 5_000 });
    await p().keyboard.press('Escape');
    assert.equal(await settingsDialog().count(), 0, 'CONTRACT (SETTINGS): Escape closed the dialog');
    record(`OFFICE-SETTINGS ${vp.width}x${vp.height}`, { focus: await focusKey(), popover: await p().locator(sel('di-scan-options-popover')).count() });
    assert.equal(await focusKey(), 'di-scan-options', 'CONTRACT (SETTINGS): focus returns to the Options trigger');
  });
}
for (const vp of TWO_PANE) {
  test(`B3 MENUS, office Home beside New Mission, at ${vp.width}x${vp.height} (${vp.role}), ESCAPE: consumed, the screen beside it is kept`, async () => {
    await boot(vp);
    assert.equal(await p().locator(sel('di-new-mission')).count(), 1, 'STATE: New Mission is open beside the list');
    await p().click(sel('di-scan-options'));
    await p().waitForSelector(sel('di-scan-options-popover'));
    await p().keyboard.press('Escape');
    assert.equal(await p().locator(sel('di-scan-options-popover')).count(), 0, 'CONTRACT (ESCAPE): the popover closed');
    assert.equal(await p().locator(sel('di-new-mission')).count(), 1, 'CONTRACT (ESCAPE): the screen is KEPT (the press was consumed)');
    assert.equal(await focusKey(), 'di-scan-options', 'CONTRACT (ESCAPE): focus is on the trigger');
  });
}
