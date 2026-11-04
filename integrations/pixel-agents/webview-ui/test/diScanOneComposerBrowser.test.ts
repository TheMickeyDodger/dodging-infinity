/**
 * B8: ONE screen, ONE composer, as RENDERED, when New Mission and the request workspace meet (Free UI streamline, 2026-11-04, approved Mission mn-92b246d…, Lead1 addendum 2).
 *
 * New Mission's Ask box and the workspace's Ask composer edit ONE draft (the workspace's own composer state). Sharing state is not the guarantee: two boxes can both be on screen and
 * editable at once, and the workspace can cover New Mission. So every count here is of the RENDERED page: a composer counts as visible only where the browser draws it on top
 * (`elementFromPoint` at points inside it, after it is scrolled into view), and as editable only where it is not read-only, disabled or inert.
 *
 * B8.1 the workspace is ALREADY open on Ask with an unsent draft, and the person enters New Orchestrator from the rail;
 * B8.2 the person drafts on New Mission, then opens the draft in the workspace to submit (DI_DRAFT_OPEN_LABEL), types a follow-up there, and enters New Orchestrator again;
 * B8.3 the exact text survives both routes;
 * B8.4 Requests, Approve and Start stay reachable BY KEYBOARD, in the workspace, and keep their authority (Approve needs the typed word; each sends exactly its own one op); nothing
 *      on New Mission submits, approves or starts.
 * Each at a WIDE window (1440 x 900: the workspace is docked beside the office panel, over the open stage) and a NARROW one (900 x 800: the workspace covers the window).
 *
 * The office state is the session-mismatch v2 fixture (real projection output), in its labelled available-capacity variation. Every request-panel answer is LABELLED SYNTHETIC
 * (test/diNewMissionSynthetic.ts, the panel's own wire shape); a submit, a decision and a start are recorded and never answered. Nothing is live.
 *
 * Captures: with DI_STREAMLINE_SHOTS=<absolute dir>, the views are written there with one-composer-manifest.json (each with what was measured). Without it, nothing is written.
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
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  GATE_LABEL,
  relayReply,
  SYNTHETIC_APPROVED_REF,
  SYNTHETIC_PENDING_REF,
  syntheticConversation,
  syntheticDraftedTurn,
  syntheticOffer,
  syntheticOverviewWithRequests,
  syntheticRequest,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FIXTURE = 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json';
const office = availableCapacityOffice((JSON.parse(fs.readFileSync(path.join(root, FIXTURE), 'utf-8')) as { state: DiOfficeState }).state);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env['DI_STREAMLINE_SHOTS'] ?? null;
if (SHOTS !== null && !path.isAbsolute(SHOTS)) throw new Error('DI_STREAMLINE_SHOTS must be an absolute directory');

const VIEWPORTS = [
  { name: 'wide', width: 1440, height: 900 },
  { name: 'narrow', width: 900, height: 800 },
] as const;
/** An unsent draft that loses something visible if any surface trims, normalises, truncates or replaces it. */
const DRAFT = '  Unsent draft — typed in the workspace.\nSecond line: ünïcödé ✓, two  spaces.  ';
const ASK = 'SYNTHETIC ask: make the readiness probe stable — once.';
const FOLLOW = 'A follow-up, unsent: also cover the readiness flake ✓';

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let sentByPage: Array<Record<string, unknown>> = [];
let sentTurn: string | null = null;
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
        // LABELLED SYNTHETIC answers, in the panel's own wire shape; submit, decide and run are recorded and never answered
        const answer = (a: Record<string, unknown>) => ws.send(JSON.stringify(relayReply(msg.seq, String(msg.op), a)));
        if (msg.op === 'refresh') answer(syntheticOverviewWithRequests([SYNTHETIC_PENDING_REF, SYNTHETIC_APPROVED_REF]));
        if (msg.op === 'request') answer(syntheticRequest(String(msg.requestRef)));
        if (msg.op === 'offer') answer(syntheticOffer(String(msg.requestRef)));
        if (msg.op === 'turn') {
          sentTurn = String(msg.text);
          answer({ ok: true, turn: syntheticTurn(sentTurn) });
        }
        if (msg.op === 'conversation' && sentTurn !== null) answer(syntheticConversation([syntheticDraftedTurn(sentTurn)]));
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
  if (SHOTS !== null && shots.length > 0)
    fs.writeFileSync(
      path.join(SHOTS, 'one-composer-manifest.json'),
      `${JSON.stringify(
        {
          fixture: true,
          label:
            'FIXTURE office state + LABELLED SYNTHETIC request-panel replies (webview-ui/test/diScanOneComposerBrowser.test.ts). The real App mounted from source by the Vite dev server; NOT a live backend, NOT the production bundle.',
          office: { path: `webview-ui/${FIXTURE}`, sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, FIXTURE))).digest('hex'), variation: 'availableCapacityOffice' },
          shots,
        },
        null,
        2,
      )}\n`,
    );
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (one composer): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
  sentTurn = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const ops = () => sentByPage.filter((m) => m.type === 'diMissionRequest').map((m) => String(m.op));
const writes = () => sentByPage.filter((m) => m.type === 'diMissionRequest' && !['refresh', 'request', 'offer', 'conversation'].includes(String(m.op)));

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: viewport.width, height: viewport.height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => document.querySelector('[data-testid="di-new-recent-not-read"]') === null);
}

interface ComposerReading {
  id: string;
  present: boolean;
  /** drawn at all: a box, not hidden */
  rendered: boolean;
  /** of the points inside it (after it is scrolled into view), how many the browser draws it on top at */
  onTop: number;
  points: number;
  editable: boolean;
  value: string | null;
}
/** Every Ask composer on the page, as RENDERED. */
const composers = (): Promise<ComposerReading[]> =>
  p().evaluate(() =>
    ['di-new-ask', 'di-mr-composer'].map((id) => {
      const el = document.querySelector<HTMLTextAreaElement>(`[data-testid="${id}"]`);
      if (el === null) return { id, present: false, rendered: false, onTop: 0, points: 0, editable: false, value: null };
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const rendered = el.getClientRects().length > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 0 && r.height > 0;
      const pts = [[0.5, 0.5], [0.15, 0.25], [0.85, 0.25], [0.15, 0.75], [0.85, 0.75]]
        .map(([fx, fy]) => [r.left + r.width * fx!, r.top + r.height * fy!] as const)
        .filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
      const onTop = pts.filter(([x, y]) => {
        const at = document.elementFromPoint(x, y);
        return at === el || (at !== null && el.contains(at));
      }).length;
      const editable = !el.readOnly && !el.disabled && el.closest('[inert]') === null;
      return { id, present: true, rendered, onTop, points: pts.length, editable, value: el.value };
    }),
  );
const visibleEditable = (readings: ComposerReading[]) => readings.filter((c) => c.rendered && c.onTop > 0 && c.editable).map((c) => c.id);
/** What share of a 6 x 6 grid over the New Mission stage (inside the window) the browser draws New Mission's own stage at: 1 is not obscured at all. */
const newMissionUncovered = () =>
  p().evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"][data-view="new"]');
    if (stage === null) return { share: 0, points: 0, hitOther: [] as string[] };
    const r = stage.getBoundingClientRect();
    const left = Math.max(0, r.left), right = Math.min(innerWidth, r.right), top = Math.max(0, r.top), bottom = Math.min(innerHeight, r.bottom);
    let inside = 0, total = 0;
    const other = new Set<string>();
    for (let i = 0; i < 6; i++)
      for (let j = 0; j < 6; j++) {
        const x = left + ((right - left) * (i + 0.5)) / 6, y = top + ((bottom - top) * (j + 0.5)) / 6;
        const at = document.elementFromPoint(x, y);
        total++;
        if (at !== null && stage.contains(at)) inside++;
        else other.add(at?.closest('[data-testid]')?.getAttribute('data-testid') ?? at?.tagName ?? 'nothing');
      }
    return { share: inside / total, points: total, hitOther: [...other] };
  });
/** Enter New Orchestrator from the rail: by pointer where the rail entry is drawn on top, by keyboard (focus, Enter) where something covers it. */
async function enterNewOrchestrator(): Promise<'pointer' | 'keyboard'> {
  const reachable = await p().locator(sel('di-rail-new-orchestrator')).evaluate((el) => {
    const r = el.getBoundingClientRect();
    const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return at === el || (at !== null && el.contains(at));
  });
  if (reachable) {
    await p().click(sel('di-rail-new-orchestrator'));
    return 'pointer';
  }
  await p().locator(sel('di-rail-new-orchestrator')).focus();
  await p().keyboard.press('Enter');
  return 'keyboard';
}
/** Wait (bounded) until `cond` holds on what the page has sent. */
async function until(what: string, cond: () => boolean, ms = 10_000): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}: ${JSON.stringify(ops())}`);
    await new Promise((r) => setTimeout(r, 50));
  }
}
async function tabTo(testid: string, max = 160): Promise<void> {
  for (let i = 0; i < max; i++) {
    if ((await p().evaluate(() => document.activeElement?.getAttribute('data-testid'))) === testid) return;
    await p().keyboard.press('Tab');
  }
  throw new Error(`${testid} was not reached by Tab`);
}
async function snap(name: string, note: string, measured: Record<string, unknown>): Promise<void> {
  console.log(`[B8 ${name}]`, JSON.stringify(measured));
  if (SHOTS === null) return;
  const file = `${name}.png`;
  await p().screenshot({ path: path.join(SHOTS, file) });
  shots.push({ file, note, viewport: p().viewportSize(), measured, sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(SHOTS, file))).digest('hex') });
}

/**
 * B8.4: Requests, Approve and Start, by keyboard, in the workspace, each with its own authority. Reached from New Mission's "Review requests" by Tab and Enter. Approve stays held until
 * the exact word is typed, then sends ONE decide for the request on display; Start sends ONE run (dispatch) for the approved one. Neither is offered on New Mission.
 */
async function gatesByKeyboard(label: string): Promise<void> {
  // the gates are not on New Mission
  const onNewMission = await p()
    .locator('[data-testid="di-new-mission"] button')
    .evaluateAll((buttons) => buttons.map((b) => (b.textContent ?? '').trim()));
  const gatesHere = onNewMission.filter((t) => GATE_LABEL.test(t));
  assert.deepEqual(gatesHere, [], `${label}: no gate on New Mission (its buttons: ${JSON.stringify(onNewMission)})`);
  await p().locator(sel('di-new-ask')).focus();
  await tabTo('di-new-open-workspace');
  await p().keyboard.press('Enter');
  await p().waitForSelector(sel('di-mr-tab-requests'));
  assert.equal(await p().locator(sel('di-mr-tab-requests')).getAttribute('aria-selected'), 'true', `${label}: Requests reached by keyboard`);
  const before = writes().length;
  await tabTo(`di-mr-request-${SYNTHETIC_PENDING_REF}`);
  await p().keyboard.press('Enter');
  await p().waitForSelector(sel('di-mr-word'), { timeout: 20_000 });
  // Approve carries its authority: held until the exact word is typed, by the person
  assert.equal(await p().locator(sel('di-mr-approve')).getAttribute('aria-disabled'), 'true', `${label}: Approve is held before the word`);
  await tabTo('di-mr-word');
  await p().keyboard.type('approve');
  await tabTo('di-mr-approve');
  await p().waitForFunction(() => document.querySelector('[data-testid="di-mr-approve"]')?.getAttribute('aria-disabled') === null);
  await p().keyboard.press('Enter');
  await until('the decision', () => writes().length > before);
  const decided = writes().slice(before);
  assert.deepEqual(decided.map((m) => [m.op, m.requestRef]), [['decide', SYNTHETIC_PENDING_REF]], `${label}: ONE decision, for the request on display: ${JSON.stringify(decided)}`);
  // Start, for the approved request
  await p().locator(sel(`di-mr-request-${SYNTHETIC_APPROVED_REF}`)).focus();
  await p().keyboard.press('Enter');
  await p().waitForSelector(sel('di-mr-dispatch'), { timeout: 20_000 });
  const beforeStart = writes().length;
  await tabTo('di-mr-dispatch');
  await p().keyboard.press('Enter');
  await until('the start', () => writes().length > beforeStart);
  const started = writes().slice(beforeStart);
  assert.deepEqual(started.map((m) => [m.op, m.requestRef, m.command]), [['run', SYNTHETIC_APPROVED_REF, 'dispatch']], `${label}: ONE start, for the approved request: ${JSON.stringify(started)}`);
}

for (const viewport of VIEWPORTS) {
  test(
    `B8.1 + B8.3 + B8.4 at ${viewport.width} x ${viewport.height} (${viewport.name}): the workspace ALREADY open on Ask with an unsent draft; New Orchestrator from the rail leaves ONE visible, editable composer holding that exact draft, and New Mission not obscured`,
    async () => {
      await boot(viewport);
      // the workspace open on Ask, with an UNSENT draft typed in its composer
      await p().click(sel('di-new-open-workspace'));
      await p().click(sel('di-mr-tab-ask'));
      await p().locator(sel('di-mr-composer')).fill(DRAFT);
      const open = await composers();
      await snap(`b8-1-${viewport.name}-a-workspace-open`, 'The workspace open on Ask with an unsent draft, before New Orchestrator is entered.', { composers: open, visibleEditable: visibleEditable(open) });

      const entry = await enterNewOrchestrator();
      await p().waitForSelector(sel('di-new-mission'));
      await p().waitForTimeout(300);
      const after = await composers();
      const uncovered = await newMissionUncovered();
      await snap(`b8-1-${viewport.name}-b-after-new-orchestrator`, `After New Orchestrator (entered by ${entry}).`, { entry, composers: after, visibleEditable: visibleEditable(after), uncovered });

      assert.deepEqual(visibleEditable(after), ['di-new-ask'], `ONE visible, editable composer: ${JSON.stringify(after)}`);
      assert.equal(uncovered.share, 1, `New Mission is not obscured: ${JSON.stringify(uncovered)}`);
      // B8.3: the exact draft, retained, in the one composer; never cleared, cut or replaced
      assert.equal(after.find((c) => c.id === 'di-new-ask')!.value, DRAFT, 'the unsent draft, exactly');
      assert.deepEqual(writes(), [], 'nothing was sent');

      await gatesByKeyboard(`route 1, ${viewport.name}`);
      assert.deepEqual(
        writes().map((m) => m.op),
        ['decide', 'run'],
        'only the two gates the person operated in the workspace wrote anything; New Mission wrote nothing',
      );
      // B8.3: after the gates, the one draft is still exactly what was typed (read in the workspace's Ask, where the person now is)
      await p().click(sel('di-mr-tab-ask'));
      assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), DRAFT, 'still the exact draft after the gates');
    },
    STEPS_MS,
  );

  test(
    `B8.2 + B8.3 + B8.4 at ${viewport.width} x ${viewport.height} (${viewport.name}): drafted on New Mission, then opened in the workspace to submit: the ask is not asked for twice and there is ONE visible, editable composer; back on New Mission the follow-up is kept exactly`,
    async () => {
      await boot(viewport);
      await p().locator(sel('di-new-ask')).fill(ASK);
      await p().locator(sel('di-new-send')).click();
      await p().waitForSelector(sel('di-new-draft-open'), { timeout: 20_000 });
      await p().locator(sel('di-new-draft-open')).focus();
      await p().keyboard.press('Enter');
      await p().waitForSelector(sel('di-mr-draft'));
      const inWorkspace = await composers();
      await snap(`b8-2-${viewport.name}-a-opened-to-submit`, 'The draft opened in the workspace to submit (the same conversation).', { composers: inWorkspace, visibleEditable: visibleEditable(inWorkspace) });
      assert.deepEqual(visibleEditable(inWorkspace), ['di-mr-composer'], `ONE visible, editable composer: the workspace's own: ${JSON.stringify(inWorkspace)}`);
      // the ask is not asked for twice: it is in the conversation, once, and no composer holds it again
      for (const c of inWorkspace) if (c.present) assert.equal(c.value, '', `${c.id} does not ask for the ask again`);
      assert.match((await p().locator(`${sel('di-mr-turn-1')} [data-who="you"]`).textContent()) ?? '', /make the readiness probe stable — once\./);
      assert.equal(sentByPage.filter((m) => m.op === 'turn').length, 1, 'one turn');
      assert.ok(await p().locator(sel('di-mr-submit')).isVisible(), 'Submit is there, in the workspace');

      await p().locator(sel('di-mr-composer')).fill(FOLLOW);
      const entry = await enterNewOrchestrator();
      await p().waitForSelector(sel('di-new-mission'));
      await p().waitForTimeout(300);
      const back = await composers();
      const uncovered = await newMissionUncovered();
      await snap(`b8-2-${viewport.name}-b-back-on-new-mission`, `Back on New Mission (entered by ${entry}).`, { entry, composers: back, visibleEditable: visibleEditable(back), uncovered });
      assert.deepEqual(visibleEditable(back), ['di-new-ask'], `ONE visible, editable composer: ${JSON.stringify(back)}`);
      assert.equal(uncovered.share, 1, `New Mission is not obscured: ${JSON.stringify(uncovered)}`);
      assert.equal(back.find((c) => c.id === 'di-new-ask')!.value, FOLLOW, 'B8.3: the follow-up, exactly');
      assert.equal(await p().locator(sel('di-new-draft')).count(), 1, 'the draft is still in place');
      assert.deepEqual(writes().map((m) => m.op), ['turn'], `nothing but the one drafting turn: ${JSON.stringify(ops())}`);

      // B8.4 on this route too: Requests, Approve and Start by keyboard, in the workspace, each with its authority
      await gatesByKeyboard(`route 2, ${viewport.name}`);
      assert.deepEqual(writes().map((m) => m.op), ['turn', 'decide', 'run'], 'New Mission wrote only the drafting turn; the gates wrote only where the person operated them');
      await p().click(sel('di-mr-tab-ask'));
      assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), FOLLOW, 'B8.3: still the follow-up, exactly, after the gates');
    },
    STEPS_MS,
  );
}
