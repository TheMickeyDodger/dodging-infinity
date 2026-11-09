/**
 * K1, RENDERED in Chrome (not Brave evidence): keyboard ownership while the Map is OPEN, under the Supervisor's ruling
 * (.herd/state/k1-keyboard-ownership-ruling.md). The real App (test/harness/di-app-scan.html), the session-mismatch v2 FIXTURE (available capacity),
 * the large world (packaging/di/worlds/living-metropolis.json), the request panel SYNTHETIC. Nothing is live. NOT attribution (that is the matched
 * pair mapkeys-measure-HEAD / -LIVE-chrome.log, never re-run into).
 *
 * Each claim names the assertion that carries it (method rule 2), and every claim is about the KEYS (focus placed directly is only setup):
 *  - M1: two panes, the Map open and UNCOVERED, focus in Home: Cmd+N (an APPLICATION command, not the browser) opens New Mission;
 *  - HOME-OWNED ESCAPE: focus in Home's (empty) search, Escape: the Map STAYS open and focus stays where it was (a no-op at Home's root is valid);
 *  - M4: the Map beneath a docked Mission (1440, 1280), focus on Back: Cmd+N opens New Mission;
 *  - ORDERING: Escape dismisses the docked Mission to Home and does NOT close the Map on the same press; the next, Home-owned, Escape does not either;
 *  - COVERED, BODY FOCUS: a docked Mission over the open Map, focus fallen to the body: Escape peels the SCREEN (coverage owns), the Map stays open;
 *  - COVERED BY THE MIGRATION NOTICE, BODY FOCUS (the Lead's ruling (ii)): the notice covers the open Map and claims no front, so it states the covering
 *    contract; Escape under it is a NO-OP (the Map stays, the notice stays). CONTROL in the same test: the same body-focus Escape once the notice is
 *    dismissed closes the Map, so the no-op is the cover's, not a dead key;
 *  - M2: a Mission docked, the panel MINIMIZED, the Map opened from its toggle: Map-owned Escape closes the MAP, consumed (the hidden stage is not peeled);
 *  - M3: 1000 px, a covering request workspace over the open Map, focus in it: the workspace itself receives keys; Escape dismisses the WORKSPACE's
 *    layer only (the workspace closes, the New Mission screen beneath stays, focus returns to the control that opened it), and the Map stays open;
 *  - A HIDDEN STAGE OWNS NOTHING (the Lead's ruling on the defect K1 introduced: hidden, minimized or unmounted content claims no keys): a Mission
 *    docked, the panel MINIMIZED. Escape with focus on the office's restore control (where minimizing puts it) or on an agent's Mission label, with
 *    the Map open, and with the Map CLOSED (the inherited case, attributed on the bases): the hidden stage is NOT peeled. Cmd+N with the panel
 *    hidden, the Map open and closed: the hidden panel does NOT switch to New Mission (a no-op, point 6). The hidden stage is asserted FIRST;
 *  - THE KEYBOARD ROUTE (ruling 7): from Home's search, SEQUENTIAL Shift+Tab reaches the Map's labelled toggle; Map-owned Escape there closes the Map
 *    with focus left on the toggle; sequential Tab returns to Home's search. Programmatic focus is never the route;
 *  - ISOLATION (ruling 3), bracketed against a clamped camera: the world-pan LISTENER RUNS with the Map closed (before), does NOT run with the Map open
 *    while the app receives the key, and RUNS again with the Map closed (after). What it observes is
 *    LISTENER EXECUTION AND STATE CHANGE, never camera motion: the listener clears `cameraFollowId` BEFORE `clampPanToWorld` (App.tsx), so a camera
 *    clamped at its edge still trips the marker where coordinates would not move. No coordinate is measured, so no motion is claimed.
 *    The editor's keys behind an open Map are NOT tested here: that state is STRUCTURALLY IMPOSSIBLE, and a read of it could never fail (the Lead's
 *    ruling: zero information, never a green line). The editor installs its only key listener in edit mode alone (useEditorKeyboard.ts, the effect
 *    returns before addEventListener); the Map mounts only outside edit mode (App.tsx, `!editor.isEditMode` on <WorldMap>); its toggle is offered only
 *    outside edit mode (`mapAvailable`); entering edit mode closes the Map; App's only window key listener is the world-pan, which writes no layout.
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
import { relayReply, syntheticOverview } from './diNewMissionSynthetic.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const metropolis = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', syntheticOverview())));
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
    console.warn(`SKIPPED (K1 keyboard ownership): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const mapDrawn = async () => (await p().locator(sel('world-map')).count()) === 1;
const screenView = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);
const record = (name: string, r: Record<string, unknown>) => console.log(`K1AFTER ${name}: ${JSON.stringify(r)}`);

async function boot(width: number, height: number, opts: { wasReset?: boolean } = {}): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  // wasReset: the server reporting a layout reset, which shows the (upstream) migration notice over the world
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis), ...(opts.wasReset === true ? { wasReset: true } : {}) }));
  await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, {
    timeout: 60_000,
  });
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}
async function openMap(): Promise<void> {
  if ((await p().locator(sel('world-view-map')).count()) === 0) await p().click(sel('di-world-disclosure'));
  await p().click(sel('world-view-map'));
  await p().waitForSelector(sel('world-map'));
}
async function dockFirst(): Promise<void> {
  await p().click(sel('di-scan-row'));
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}
async function handlersReceiveKeys(): Promise<boolean> {
  await p().evaluate(() => {
    if (document.documentElement.dataset.keysSeen === undefined) {
      document.documentElement.dataset.keysSeen = '0';
      window.addEventListener('keydown', () => {
        document.documentElement.dataset.keysSeen = String(Number(document.documentElement.dataset.keysSeen) + 1);
      });
    }
  });
  const read = () => p().evaluate(() => Number(document.documentElement.dataset.keysSeen));
  const before = await read();
  await p().keyboard.press('ArrowDown');
  return (await read()) > before;
}
/**
 * Does a key reach an ELEMENT's own listener? The request workspace stops every key at its root on purpose (`DiMissionRequest.tsx`: "keys typed in
 * the workspace belong to it"), so no window listener can ever see them, with or without the Map: a window probe there is the WRONG instrument (it
 * read false for that reason in the first K1 run). A native listener ON the element runs after the window's capture phase (where the Map's guard
 * acts) and before React's own handler: it sees the key exactly when the guard yielded.
 */
async function elementReceivesKeys(selector: string): Promise<boolean> {
  await p().evaluate((s) => {
    const el = document.querySelector(s) as HTMLElement;
    if (el.dataset.keysSeen === undefined) {
      el.dataset.keysSeen = '0';
      el.addEventListener('keydown', () => {
        el.dataset.keysSeen = String(Number(el.dataset.keysSeen) + 1);
      });
    }
  }, selector);
  const read = () => p().evaluate((s) => Number((document.querySelector(s) as HTMLElement).dataset.keysSeen), selector);
  const before = await read();
  await p().keyboard.press('ArrowDown');
  return (await read()) > before;
}
async function cmdN(): Promise<string | null> {
  await p().keyboard.press('Meta+n');
  await p().waitForTimeout(400);
  return screenView();
}
const settle = () => p().waitForTimeout(300);

test('M1: two panes, the Map open and UNCOVERED, focus in Home: Cmd+N (an application command) opens New Mission, and the page receives keys', async () => {
  await boot(1440, 900);
  await openMap();
  await p().locator(sel('di-scan-search')).focus();
  const receives = await handlersReceiveKeys();
  const view = await cmdN();
  record('M1', { receives, cmdN: view, mapDrawn: await mapDrawn() });
  assert.equal(receives, true, 'the page receives keys with focus in Home');
  assert.equal(view, 'new', 'Cmd+N opened New Mission');
  assert.equal(await mapDrawn(), true, 'the Map is still open (nothing dismissed it)');
}, STEPS_MS);

test('HOME-OWNED ESCAPE: focus in Home\'s (empty) search beside the open Map: Escape leaves the Map OPEN and focus where it was (a no-op at Home\'s root)', async () => {
  await boot(1440, 900);
  await openMap();
  await p().locator(sel('di-scan-search')).focus();
  await p().keyboard.press('Escape');
  await settle();
  const r = { mapDrawn: await mapDrawn(), focus: await focusNow(), view: await screenView() };
  record('Home-owned Escape', r);
  assert.deepEqual(r, { mapDrawn: true, focus: 'di-scan-search', view: null }, 'nothing closed; focus intentional');
}, STEPS_MS);

for (const [w, h] of [
  [1440, 900],
  [1280, 800],
] as const) {
  test(`M4 at ${w}x${h}: the Map beneath a docked Mission, focus on Back: Cmd+N opens New Mission`, async () => {
    await boot(w, h);
    await openMap();
    await dockFirst();
    await p().locator(sel('di-scan-back')).focus();
    const view = await cmdN();
    record(`M4 @${w}`, { cmdN: view, mapDrawn: await mapDrawn() });
    assert.equal(view, 'new', 'Cmd+N opened New Mission');
    assert.equal(await mapDrawn(), true, 'the Map is still open beneath');
  }, STEPS_MS);

  test(`ORDERING at ${w}x${h}: Escape dismisses the docked Mission to Home and does NOT close the Map; the next, Home-owned, Escape does not either`, async () => {
    await boot(w, h);
    await openMap();
    await dockFirst();
    await p().locator(sel('di-scan-back')).focus();
    await p().keyboard.press('Escape');
    await settle();
    const first = { view: await screenView(), mapDrawn: await mapDrawn(), focus: await focusNow() };
    await p().keyboard.press('Escape');
    await settle();
    const second = { view: await screenView(), mapDrawn: await mapDrawn(), focus: await focusNow() };
    record(`ordering @${w}`, { first, second });
    assert.equal(first.view, null, 'the first Escape dismissed the Mission');
    assert.equal(first.mapDrawn, true, 'the SAME press did not close the Map');
    assert.equal(first.focus, 'di-scan-row', 'focus on the dismissed Mission\'s row, in Home');
    assert.equal(second.mapDrawn, true, 'the next, Home-owned, Escape did not close the Map either');
  }, STEPS_MS);
}

test('COVERED, BODY FOCUS: a docked Mission over the open Map, focus fallen to the body: Escape peels the SCREEN, the Map stays open', async () => {
  await boot(1440, 900);
  await openMap();
  await dockFirst();
  await p().evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  assert.equal(await focusNow(), 'BODY', 'focus on the body');
  await p().keyboard.press('Escape');
  await settle();
  const r = { view: await screenView(), mapDrawn: await mapDrawn() };
  record('covered, body focus', r);
  assert.deepEqual(r, { view: null, mapDrawn: true }, 'the covering screen owned Escape; the Map stays open');
}, STEPS_MS);

for (const chrome of ['drawn', 'collapsed'] as const)
  test(`ROUND 17, UNCOVERED, BODY FOCUS, the toggle ${chrome.toUpperCase()}: Map-owned Escape closes the Map and gives focus an intentional visible destination`, async () => {
    await boot(1440, 900);
    await openMap();
    if (chrome === 'collapsed') {
      // the world's chrome collapsed while the Map stays open: the toggle is not drawn (App.tsx, toggleWorldChrome closes only the City panel)
      await p().click(sel('di-world-disclosure'));
      await settle();
      assert.equal(await p().locator(sel('world-view-map')).count(), 0, 'SETUP: the toggle is not drawn');
    }
    assert.equal(await mapDrawn(), true, 'SETUP: the Map is open');
    await p().evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    assert.equal(await focusNow(), 'BODY', 'SETUP: focus has fallen to the body (read BEFORE the key)');
    await p().keyboard.press('Escape');
    await settle();
    const r = { mapDrawn: await mapDrawn(), focusAfter: await focusNow() };
    record(`round 17 body-focus close, toggle ${chrome}`, r);
    const destination = chrome === 'drawn' ? 'world-view-map' : 'di-world-disclosure';
    assert.deepEqual(r, { mapDrawn: false, focusAfter: destination }, `the Map closed, and focus is on ${destination} (read AFTER the key), never the body`);
  }, STEPS_MS);

test('COVERED BY THE MIGRATION NOTICE, BODY FOCUS: Escape under the notice is a NO-OP (the Map stays, the notice stays); dismissed, the same Escape closes the Map', async () => {
  await boot(1440, 900, { wasReset: true });
  const NOTICE = 'text=We owe you an apology!';
  await p().waitForSelector(NOTICE);
  // SETUP: the notice intercepts the pointer, so the Map is opened from its controls by keyboard (focus placed directly is only setup here)
  if ((await p().locator(sel('world-view-map')).count()) === 0) {
    await p().locator(sel('di-world-disclosure')).focus();
    await p().keyboard.press('Enter');
    await p().waitForSelector(sel('world-view-map'));
  }
  await p().locator(sel('world-view-map')).focus();
  await p().keyboard.press('Enter');
  await p().waitForSelector(sel('world-map'));
  const toBody = () => p().evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await toBody();
  const covered = { focus: await focusNow(), noticeCovers: await p().evaluate(() => [...document.querySelectorAll('[data-di-covers-world]')].some((e) => e.textContent?.includes('We owe you an apology!') === true)) };
  await p().keyboard.press('Escape');
  await settle();
  const under = { mapDrawn: await mapDrawn(), noticeShown: (await p().locator(NOTICE).count()) === 1, focusAfter: await focusNow() };
  await p().getByRole('button', { name: 'Got it' }).click();
  await p().waitForSelector(NOTICE, { state: 'detached' });
  await toBody();
  // ROUND 17 (the Reviewer's blocker): this read was once reported as the control's OUTCOME, though it was taken BEFORE Escape (measuring the wrong
  // moment: it agreed whenever the starting focus was acceptable). It is now SETUP, asserted before the key, and the outcome is read AFTER it.
  assert.equal(await focusNow(), 'BODY', 'SETUP: dismissed, focus has fallen to the body');
  await p().keyboard.press('Escape');
  await settle();
  const control = { mapDrawn: await mapDrawn(), focusAfter: await focusNow() };
  record('covered by the migration notice', { covered, under, control });
  assert.deepEqual(covered, { focus: 'BODY', noticeCovers: true }, 'focus has fallen to the body under the notice, which states the covering contract');
  assert.deepEqual(
    under,
    { mapDrawn: true, noticeShown: true, focusAfter: 'BODY' },
    'Escape under the notice is a no-op: the Map stays open, the notice stays, and a no-op moves no focus (the notice owns no Escape)',
  );
  assert.deepEqual(
    control,
    { mapDrawn: false, focusAfter: 'world-view-map' },
    'CONTROL: dismissed, the same body-focus Escape closes the uncovered Map, and focus goes to its toggle, never left on the body',
  );
}, STEPS_MS);

test('M2: a Mission docked, the panel MINIMIZED, the Map opened from its toggle: Map-owned Escape closes the MAP, consumed (the hidden stage is not peeled)', async () => {
  await boot(1440, 900);
  await dockFirst();
  await p().click(sel('di-office-panel-minimize'));
  await settle();
  assert.equal(await p().evaluate(() => (document.querySelector('.di-scan') as HTMLElement | null)?.hidden ?? null), true, 'the panel is minimized');
  await openMap();
  assert.equal(await focusNow(), 'world-view-map', 'focus on the Map\'s toggle (the click put it there)');
  await p().keyboard.press('Escape');
  await settle();
  const r = { mapDrawn: await mapDrawn(), hiddenStageView: await screenView(), focus: await focusNow() };
  record('M2', r);
  assert.equal(r.mapDrawn, false, 'Map-owned Escape closed the Map');
  assert.equal(r.hiddenStageView, 'mission', 'and only the Map: the hidden stage was not peeled');
  assert.equal(r.focus, 'world-view-map', 'focus intentional, on the toggle');
}, STEPS_MS);

test('M3: 1000 px, a covering request workspace over the open Map, focus in it: its handlers receive keys and Escape leaves the Map open', async () => {
  await boot(1000, 800);
  await openMap();
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-new-open-workspace"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector('.di-mr[data-covering="true"]');
  // ON the workspace (it stops keys at its root, so a window probe is blind there; see elementReceivesKeys)
  const receives = await elementReceivesKeys('.di-mr');
  await p().keyboard.press('Escape');
  await settle();
  const r = { receives, mapDrawn: await mapDrawn(), workspaceOpen: (await p().locator('.di-mr').count()) > 0, view: await screenView(), focus: await focusNow() };
  record('M3', r);
  assert.equal(receives, true, 'the workspace itself receives keys (its own listener, after the Map\'s guard)');
  assert.equal(r.workspaceOpen, false, 'Escape dismissed the workspace\'s layer: the workspace closed');
  assert.equal(r.view, 'new', 'only that layer: the New Mission screen beneath it stays');
  // the workspace's close returns focus to its opener when shown (DiMissionRequest.tsx, the unmount of its focus effect; App.tsx missionOpener)
  assert.equal(r.focus, 'di-new-open-workspace', 'focus is intentional: back on the control that opened the workspace');
  assert.equal(r.mapDrawn, true, 'Escape did not close the Map beneath the workspace');
}, STEPS_MS);

/** A Mission docked, then the panel minimized by its own control: focus goes to the office's restore control (DiOfficePanel.tsx, focusExposed). */
async function dockThenMinimize(): Promise<void> {
  await dockFirst();
  await p().click(sel('di-office-panel-minimize'));
  await settle();
  assert.equal(await p().evaluate(() => (document.querySelector('.di-scan') as HTMLElement | null)?.hidden ?? null), true, 'SETUP: the panel is minimized');
  assert.equal(await screenView(), 'mission', 'SETUP: the hidden stage holds the docked Mission');
}
const panelHidden = () => p().evaluate(() => (document.querySelector('.di-scan') as HTMLElement | null)?.hidden ?? null);
/**
 * Focus a VISIBLE agent Mission label (programmatic focus is only setup here). The labels move with their agents, so "in view" is not a stable
 * precondition (the first run of this test found none in view, twice): (F) reads `checkVisibility` only, so any visible label exercises the same path,
 * wherever its agent stands. Waits up to 20 s for one.
 */
async function focusALabel(): Promise<void> {
  const found = await p()
    .waitForFunction(
      () => {
        const l = [...document.querySelectorAll<HTMLElement>('[data-testid="di-mission-label"]')].find((e) => e.checkVisibility({ checkVisibilityCSS: true }));
        l?.focus();
        return l !== undefined && document.activeElement === l;
      },
      undefined,
      { timeout: 20_000 },
    )
    .then(() => true)
    .catch(() => false);
  assert.equal(found, true, 'SETUP: an agent Mission label is visible and takes focus');
  assert.equal(await focusNow(), 'di-mission-label', 'SETUP: focus is on the label');
}

for (const mapState of ['open', 'closed'] as const)
  test(`A HIDDEN STAGE OWNS NOTHING, the Map ${mapState.toUpperCase()}: Escape with focus on the restore control does NOT peel the minimized panel's docked Mission`, async () => {
    await boot(1440, 900);
    if (mapState === 'open') await openMap();
    await dockThenMinimize();
    assert.equal(await focusNow(), 'di-office-panel-collapsed', 'SETUP: minimizing put focus on the restore control');
    await p().keyboard.press('Escape');
    await settle();
    const r = { hiddenStageView: await screenView(), mapDrawn: await mapDrawn(), focus: await focusNow(), panelHidden: await panelHidden() };
    record(`hidden stage, restore control, Map ${mapState}`, r);
    assert.equal(r.hiddenStageView, 'mission', 'the hidden stage was NOT peeled: the docked Mission is kept');
    assert.equal(r.mapDrawn, mapState === 'open', mapState === 'open' ? 'the Map stays open (the restore control owns its keys: the ruled cost)' : 'the Map stays closed');
    assert.equal(r.focus, 'di-office-panel-collapsed', 'focus stays on the restore control');
    assert.equal(r.panelHidden, true, 'the panel stays minimized');
  }, STEPS_MS);

test('A HIDDEN STAGE OWNS NOTHING, the Map OPEN: Escape with focus on an agent Mission label does NOT peel the minimized panel\'s docked Mission', async () => {
  await boot(1440, 900);
  await openMap();
  await dockThenMinimize();
  await focusALabel();
  await p().keyboard.press('Escape');
  await settle();
  const r = { hiddenStageView: await screenView(), mapDrawn: await mapDrawn(), focus: await focusNow(), panelHidden: await panelHidden() };
  record('hidden stage, agent label, Map open', r);
  assert.equal(r.hiddenStageView, 'mission', 'the hidden stage was NOT peeled: the docked Mission is kept');
  assert.equal(r.mapDrawn, true, 'the Map stays open (the label is world content and owns its keys: the ruled cost)');
  assert.equal(r.focus, 'di-mission-label', 'focus stays on the label');
  assert.equal(r.panelHidden, true, 'the panel stays minimized');
}, STEPS_MS);

for (const mapState of ['open', 'closed'] as const)
  test(`CMD+N WITH THE PANEL HIDDEN, the Map ${mapState.toUpperCase()}: a NO-OP (the minimized panel does not switch to a New Mission nobody can see)`, async () => {
    await boot(1440, 900);
    if (mapState === 'open') await openMap();
    await dockThenMinimize();
    assert.equal(await focusNow(), 'di-office-panel-collapsed', 'SETUP: minimizing put focus on the restore control');
    await p().keyboard.press('Meta+n');
    await p().waitForTimeout(400);
    const r = { hiddenStageView: await screenView(), panelHidden: await panelHidden(), mapDrawn: await mapDrawn(), focus: await focusNow() };
    record(`Cmd+N, panel hidden, Map ${mapState}`, r);
    assert.equal(r.hiddenStageView, 'mission', 'the hidden panel did NOT switch: it still holds the docked Mission');
    assert.equal(r.panelHidden, true, 'the panel stays minimized (restore-then-open is NOT K1: a question for B3)');
    assert.equal(r.mapDrawn, mapState === 'open', mapState === 'open' ? 'the Map stays open' : 'the Map stays closed');
    assert.equal(r.focus, 'di-office-panel-collapsed', 'focus stays on the restore control');
  }, STEPS_MS);

test('THE KEYBOARD ROUTE (ruling 7): from Home\'s search, SEQUENTIAL Shift+Tab reaches the Map\'s labelled toggle; Escape there closes the Map; sequential Tab returns to Home', async () => {
  await boot(1440, 900);
  await openMap();
  await p().locator(sel('di-scan-search')).focus();
  let back = 0;
  for (; back < 80 && (await focusNow()) !== 'world-view-map'; back++) await p().keyboard.press('Shift+Tab');
  const toggle = p().locator(sel('world-view-map'));
  const label = { text: ((await toggle.textContent()) ?? '').trim(), pressed: await toggle.getAttribute('aria-pressed') };
  assert.equal(await focusNow(), 'world-view-map', `sequential Shift+Tab reached the Map's toggle (${back} presses)`);
  assert.deepEqual(label, { text: 'Map', pressed: 'true' }, 'a labelled control that says the Map is open');
  await p().keyboard.press('Escape');
  await settle();
  const closed = { mapDrawn: await mapDrawn(), focus: await focusNow() };
  assert.deepEqual(closed, { mapDrawn: false, focus: 'world-view-map' }, 'Map-owned Escape closed the Map, focus left on the toggle');
  let forward = 0;
  for (; forward < 80 && (await focusNow()) !== 'di-scan-search'; forward++) await p().keyboard.press('Tab');
  record('the keyboard route', { shiftTabsToToggle: back, label, closed, tabsBackToSearch: forward });
  assert.equal(await focusNow(), 'di-scan-search', `sequential Tab returned to Home's search (${forward} presses)`);
}, STEPS_MS);

test('ISOLATION (ruling 3), bracketed: the world-pan LISTENER RUNS with the Map closed, does NOT run with it open (the app receiving the key), and RUNS again closed', async () => {
  await boot(1440, 900);
  const follow = () =>
    p().evaluate(() => {
      const o = (window as unknown as { __diHarnessOffice?: { cameraFollowId: number | null; characters: Map<number, unknown> } }).__diHarnessOffice!;
      const id = [...o.characters.keys()].find((k) => k >= 0) ?? null;
      o.cameraFollowId = id;
      return id;
    });
  // LISTENER EXECUTION AND STATE CHANGE, not motion: the world-pan clears the follow before it clamps (see the header). Not camera coordinates.
  const panListenerRan = () => p().evaluate(() => (window as unknown as { __diHarnessOffice?: { cameraFollowId: number | null } }).__diHarnessOffice!.cameraFollowId === null);
  const tab = () => p().locator(sel('di-scan-tab-open')).focus();
  await tab();
  assert.notEqual(await follow(), null, 'a character to follow');
  await p().keyboard.press('ArrowRight');
  await settle();
  const before = await panListenerRan();
  await openMap();
  await tab();
  await follow();
  const receives = await handlersReceiveKeys();
  await follow();
  await p().keyboard.press('ArrowRight');
  await settle();
  const whileOpen = await panListenerRan();
  await p().click(sel('world-view-map'));
  await p().waitForFunction(() => document.querySelector('[data-testid="world-map"]') === null);
  await tab();
  await follow();
  await p().keyboard.press('ArrowRight');
  await settle();
  const after = await panListenerRan();
  record('isolation', { before, receives, whileOpen, after });
  assert.equal(before, true, 'CONTROL (before): with the Map closed, the world-pan listener runs (its follow reset, written before the clamp)');
  assert.equal(receives, true, 'with the Map open, the app receives the key');
  assert.equal(whileOpen, false, 'with the Map open, the world-pan listener does NOT run (no follow reset)');
  assert.equal(after, true, 'CONTROL (after): with the Map closed again, the world-pan listener runs');
}, STEPS_MS);
