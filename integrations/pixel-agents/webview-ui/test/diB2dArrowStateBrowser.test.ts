/**
 * B2d, MEASUREMENT (the Supervisor's source-identified hypothesis; the Lead's grant, TWO CASES), RENDERED in Chrome (headless; not Brave evidence).
 * THE HYPOTHESIS: the world-pan (App.tsx, installed while `isDiMode && largeWorld && !editMode && !mapShown`) skips only INPUT, TEXTAREA, SELECT and
 * contentEditable targets, then calls preventDefault() on the arrows and clears `cameraFollowId`. A scroller that is a plain DIV is NOT skipped, so in the
 * EXPOSED state (a LARGE world, the Map CLOSED) the background listener may CANCEL native code scrolling and act on the world instead.
 * CASE 1: the scan Code Diff's editor (B2d's NEW keyboard claim). CASE 2: the code review dialog's code pane (diCodeReview.test.ts:763-766's
 * PRE-EXISTING, accepted claim), the SAME hypothesis on a SIBLING surface: one tells whether a surface is exposed, two tell whether it is a CLASS.
 * Each case in THREE states: the EXPOSED one (the metropolis, 352 cols, the Map closed); CONTRAST, the mock's SMALL world (the state both claims were
 * recorded in: the listener never installed); CONTRAST, the Map OPEN (K1's gate: the listener off). The surrounding state is NAMED in every record (cols,
 * rows, largeWorld, mapDrawn), and the test WAITS ON ITS OWN LAYOUT before anything is read.
 * INSTRUMENTS, each at the level of its claim: the NATIVE SCROLL OUTCOME (the scroller's scrollLeft before and after the keys); the SOURCE-TRACED LISTENER
 * MARKER (a follow set before the key, which the world-pan clears BEFORE clamping: listener execution, never motion). CONTROLS: W0 (no key: the follow
 * stays); a non-form-control focus (the listener RUNS in the exposed state, so it is installed: without this, no-scroll and no-listener are the same
 * observation); a form control (the listener SKIPS). The route to each scroller is a REAL sequential Tab route. ASSERTED: only the states and the
 * controls. The outcomes are RECORDED ("B2DA ...") and are the measurement. Also: the files toggle's take on the editor bar's PATH, in px (the Lead's
 * point 4), with the toggle and, as a PROBE, without it.
 *
 * POST-FIX (the B2d world-pan fix, a separately named delta; `keepArrowsInEditor` in DiScanPanel.tsx): this file is PERMANENT, the defect's before-evidence
 * and now its guard. A guard that asserts no outcome can never go red, so the FIX's CONTRACT IS ASSERTED here: on the editor, ArrowRight scrolls the code
 * natively and never reaches the world-pan, in every state. The FIX's KEY BOUNDARY test (the Lead's constraints 1 and 2), in the exposed state: inside the
 * editor the four arrows stay with it, while Tab, Escape (the ladder) and Cmd+N still reach their listeners; OUTSIDE any scroller (the page body, a
 * button) the pan still RUNS.
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
const SHOTS = process.env.DI_B2DA_SHOTS ?? null;


let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

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
          ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...found, requestId: msg.requestId } : found));
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
    console.warn(`SKIPPED (B2d arrow state): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B2DA ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B2D SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
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

async function selectFile(name: string): Promise<void> {
  await p().locator('[data-testid="di-scan-tree-file"]', { hasText: name }).first().click();
  await p().waitForFunction((n) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(n), name, { timeout: 15_000 });
  await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
  await p().waitForTimeout(200);
}


const TOGGLE = '[data-testid="di-scan-files-toggle"]';
async function press(key: string, times = 1): Promise<void> {
  for (let i = 0; i < times; i++) await p().keyboard.press(key);
}
/** Sequential Tab from where focus is until `selector` has focus; the count of presses (fails past `max`). */
async function tabTo(selector: string, max = 80): Promise<number> {
  for (let n = 0; n <= max; n++) {
    if (await p().evaluate((s) => document.activeElement?.matches(s) === true, selector)) return n;
    await p().keyboard.press('Tab');
  }
  throw new Error(`sequential Tab did not reach ${selector} in ${max} presses`);
}

const metropolis = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
const CHARACTER = (doc.states.live as unknown as { missions: { missions: Array<{ members: { members: Array<{ characterId: number }> } }> } }).missions.missions[0]
  .members.members[0].characterId;
type World = 'large' | 'small';

/** Boot either face: the scan face (`di-app-scan.html`) or the operational panel (`di-app.html`), on the METROPOLIS (large) or the mock's layout (small). */
async function bootOn(face: 'scan' | 'panel', world: World, viewport = { width: 1440, height: 900 }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/${face === 'scan' ? 'di-app-scan.html' : 'di-app.html'}`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(doc.states.live));
  if (world === 'large') {
    socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
    // WAIT ON OUR OWN LAYOUT (the mock dispatches its own first; never assert before ours lands)
    await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, { timeout: 60_000 });
  }
  await p().waitForFunction((id) => (window as unknown as { __diHarnessOffice?: { characters: Map<number, unknown> } }).__diHarnessOffice?.characters.has(id) === true, CHARACTER, { timeout: 30_000 });
}

/** The surrounding state, named in every record (the Lead: a keyboard claim names the state it was measured in). */
const worldState = () =>
  p().evaluate(() => {
    const o = (window as unknown as { __diHarnessOffice: { layout: { cols: number; rows: number } } }).__diHarnessOffice;
    return { cols: o.layout.cols, rows: o.layout.rows, largeWorld: o.layout.cols > 64 || o.layout.rows > 64, mapDrawn: document.querySelector('[data-testid="world-map"]') !== null };
  });
type Cam = { __diHarnessOffice: { cameraFollowId: number | null } };
/** Follow a character, so the world-pan's run is observable (it clears the follow BEFORE clamping: App.tsx). Listener execution, never motion. */
const follow = () => p().evaluate((id) => void ((window as unknown as Cam).__diHarnessOffice.cameraFollowId = id), CHARACTER);
const followCleared = () => p().evaluate(() => (window as unknown as Cam).__diHarnessOffice.cameraFollowId === null);

/** One observation: with focus on `selector` (asserted), press ArrowRight `n` times; the native scroll of `scroller` and the listener marker. */
async function observe(selector: string, scroller: string, n = 6) {
  const focusIs = await p().evaluate((s) => document.activeElement?.matches(s) === true, selector);
  await follow();
  const left0 = await p().evaluate((s) => document.querySelector<HTMLElement>(s)?.scrollLeft ?? null, scroller);
  await press('ArrowRight', n);
  await p().waitForTimeout(250);
  const left1 = await p().evaluate((s) => document.querySelector<HTMLElement>(s)?.scrollLeft ?? null, scroller);
  return { focusIs, scrolled: left0 !== null && left1 !== null && left1 > left0, scrollLeft: [left0, left1], panListenerRan: await followCleared() };
}

/** W0: no key at all; the follow must STAY (the observable does not clear on its own). */
async function w0() {
  await follow();
  await p().waitForTimeout(400);
  return { followStayed: !(await followCleared()) };
}

const EDITOR = '[data-testid="di-scan-editor"]';
const REVIEW = '[data-testid="di-review-code"]';

async function openScanLongLine(mapOpen: boolean): Promise<void> {
  if (mapOpen) {
    if ((await p().locator('[data-testid="world-view-map"]').count()) === 0) await p().click('[data-testid="di-world-disclosure"]');
    await p().click('[data-testid="world-view-map"]');
    await p().waitForSelector('[data-testid="world-map"]');
  }
  await openCodeDiff();
  await selectFile('long_line.js');
}

/** Case 2's INSTALLATION control (added in run 2: run 1's controls were all INSIDE the dialog, so "the listener did not run" could not tell "the dialog stops
 * the keys" from "the listener is not installed"): a non-form-control focus OUTSIDE the dialog, in the same state, before it opens. */
async function outsideDialogControl() {
  const btn = p().locator('[data-testid="di-open-code-review"]');
  await btn.focus();
  return { focusIs: await p().evaluate(() => document.activeElement?.getAttribute('data-testid') === 'di-open-code-review'), ...(await (async () => {
    await follow();
    await press('ArrowRight', 1);
    await p().waitForTimeout(250);
    return { panListenerRan: await followCleared() };
  })()) };
}
let outside: { focusIs: boolean; panListenerRan: boolean } | null = null;

async function openReviewLongLine(mapOpen: boolean): Promise<void> {
  if (mapOpen) {
    await p().waitForSelector('[data-testid="world-view-map"]', { timeout: 30_000 });
    await p().click('[data-testid="world-view-map"]');
    await p().waitForSelector('[data-testid="world-map"]');
  }
  await p().waitForSelector('[data-testid="di-office-panel"], [data-testid="di-office-panel-collapsed"]', { timeout: 30_000 });
  const collapsed = await p().$('[data-testid="di-office-panel-collapsed"]');
  if (collapsed) await collapsed.click();
  await p().evaluate((id) => {
    const o = (window as unknown as { __diHarnessOffice: { selectedAgentId: number | null; cameraFollowId: number | null } }).__diHarnessOffice;
    o.selectedAgentId = id;
    o.cameraFollowId = id;
  }, CHARACTER);
  await p().waitForSelector('[data-testid="di-open-code-review"]', { timeout: 15_000 });
  outside = await outsideDialogControl();
  await p().click('[data-testid="di-open-code-review"]');
  await p().waitForSelector('[data-testid="di-review-tree"]', { timeout: 20_000 });
  await p().click('[data-testid="di-review-row"][data-path="web/long_line.js"]');
  await p().waitForFunction(() => (document.querySelector('[data-testid="di-review-code"]')?.getAttribute('aria-label') ?? '').includes('web/long_line.js'), undefined, { timeout: 20_000 });
}

/**
 * B3 1B: Find a file moved from the header into the More menu. A person reaches it by keys: Shift+Tab from where focus is to the More trigger (counted), Enter to open the menu (focus lands on its first item, Find a file). No click and no focus() on the entrance;
 * the presses are returned so the route is RECORDED. (A SETUP migration: it changes how the test reaches the state, never what it claims.)
 */
async function openMoreByKeys(): Promise<number> {
  const keyNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? 'none');
  let presses = 0;
  while ((await keyNow()) !== 'di-scan-diff-more' && presses < 150) {
    await p().keyboard.press('Shift+Tab');
    presses += 1;
  }
  if ((await keyNow()) !== 'di-scan-diff-more') throw new Error(`the More trigger was not reached by sequential keys in ${presses} presses`);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-scan-diff-more-popover"]');
  return presses;
}
async function activateFindByKeys(): Promise<number> {
  const presses = await openMoreByKeys();
  const first = await p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? 'none');
  if (first !== 'di-scan-diff-find') throw new Error(`Find a file does not hold focus when the menu opens (${first})`);
  await p().keyboard.press('Enter');
  return presses;
}

// ---- CASE 1: the scan Code Diff's editor (a plain DIV) ----
for (const state of [
  { world: 'large' as World, mapOpen: false, label: 'THE EXPOSED STATE: the metropolis, Map CLOSED (the world-pan installed)' },
  { world: 'small' as World, mapOpen: false, label: "CONTRAST: the mock's small world, Map closed (B2d's recorded state: no listener)" },
  { world: 'large' as World, mapOpen: true, label: "CONTRAST: the metropolis, Map OPEN (K1's gate: the listener off)" },
])
  test(`CASE 1, the Code Diff editor: ${state.label}`, async () => {
    await bootOn('scan', state.world);
    await openScanLongLine(state.mapOpen);
    const world = await worldState();
    const control0 = await w0();
    // CONTROL: a non-form-control focus (the files toggle, a button): does the listener RUN in this state?
    await p().locator(TOGGLE).focus();
    const onButton = await observe(TOGGLE, EDITOR, 1);
    // CONTROL: a form control (the tree's find input): the listener must SKIP it. CORRECTED (run 1 died here): the find box renders only while it is OPEN
    // (DiScanPanel.tsx `findOpen`), so it is opened first from the header's own control, `di-scan-diff-find`.
    // B3 1B: Find is an item of the More menu now; reached by real counted keys (the count is recorded below)
    let findEntrance = 0;
    if ((await p().locator('[data-testid="di-scan-diff-search"]').count()) === 0) findEntrance = await activateFindByKeys();
    await p().waitForSelector('[data-testid="di-scan-diff-search"]');
    await p().locator('[data-testid="di-scan-diff-search"]').focus();
    const onInput = await observe('[data-testid="di-scan-diff-search"]', EDITOR, 1);
    // THE CLAIM: a REAL sequential route from the toggle to the editor, then ArrowRight on it
    await p().locator(TOGGLE).focus();
    const tabs = await tabTo(EDITOR, 20);
    const onEditor = await observe(EDITOR, EDITOR, 6);
    await shot(`b2da-case1-${state.world}-map-${state.mapOpen ? 'open' : 'closed'}-after-arrows.png`);
    record(`case 1 ${state.world} map ${state.mapOpen ? 'open' : 'closed'}`, { world, control0, onButton, onInput, findEntrance, tabsToEditor: tabs, onEditor });
    assert.equal(world.largeWorld, state.world === 'large', `STATE: the world is ${state.world} (${world.cols} x ${world.rows})`);
    assert.equal(world.mapDrawn, state.mapOpen, `STATE: the Map is ${state.mapOpen ? 'open' : 'closed'}`);
    assert.equal(control0.followStayed, true, 'CONTROL W0: with no key, the follow stays (the observable does not clear on its own)');
    assert.equal(onInput.panListenerRan, false, 'CONTROL: on a form control the listener SKIPS (the follow stays)');
    assert.equal(onEditor.focusIs, true, 'the editor holds focus (reached by sequential Tab)');
    if (state.world === 'large' && !state.mapOpen)
      assert.equal(onButton.panListenerRan, true, 'POSITIVE CONTROL: in THIS state the listener RUNS on a non-form-control focus (so it is installed)');
    // THE FIX's CONTRACT (POST-FIX; asserted, so the defect's return goes red)
    assert.equal(onEditor.scrolled, true, 'CONTRACT: ArrowRight on the editor scrolls the code natively, in every state');
    assert.equal(onEditor.panListenerRan, false, "CONTRACT: the editor's arrows never reach the world-pan, in every state");
  }, STEPS_MS);

// ---- THE FIX's KEY BOUNDARY, in the EXPOSED state: what the editor keeps, what still passes, and the pan outside any scroller ----
async function keyRan(key: string): Promise<boolean> {
  await follow();
  await p().keyboard.press(key);
  await p().waitForTimeout(250);
  return followCleared();
}
const stageView = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
const editorHasFocus = () => p().evaluate((s) => document.activeElement?.matches(s) === true, EDITOR);
test("THE FIX's KEY BOUNDARY in the EXPOSED state: the editor keeps its four arrows; Tab, Escape and Cmd+N still pass; outside any scroller the pan RUNS", async () => {
  await bootOn('scan', 'large');
  await openScanLongLine(false);
  const world = await worldState();
  const control0 = await w0();
  // OUTSIDE ANY SCROLLER (constraint 2, the inverted control): the page body, then a button
  await p().evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const onBody = { focusIsBody: await p().evaluate(() => document.activeElement === document.body), panListenerRan: await keyRan('ArrowRight') };
  await p().locator(TOGGLE).focus();
  const onToggle = { focusIs: await p().evaluate((s) => document.activeElement?.matches(s) === true, TOGGLE), panListenerRan: await keyRan('ArrowLeft') };
  // INSIDE THE EDITOR: each of the four arrows the pan would take
  await p().locator(TOGGLE).focus();
  const tabs = await tabTo(EDITOR, 20);
  const arrows: Record<string, { panListenerRan: boolean }> = {};
  for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp']) arrows[key] = { panListenerRan: await keyRan(key) };
  const stillInEditor = await editorHasFocus();
  // TAB still moves focus off the editor, and Shift+Tab brings it back
  await p().keyboard.press('Tab');
  const tabLeftEditor = !(await editorHasFocus());
  await p().keyboard.press('Shift+Tab');
  const shiftTabBack = await editorHasFocus();
  // ESCAPE from inside the editor: the ladder still peels ONE layer (diff -> mission)
  const viewBeforeEscape = await stageView();
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]', { timeout: 5_000 }).catch(() => undefined);
  const viewAfterEscape = await stageView();
  // CMD+N from inside the editor (the K1 tests' own key form): New Mission still opens
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await selectFile('long_line.js');
  await p().locator(TOGGLE).focus();
  await tabTo(EDITOR, 20);
  const editorBeforeCmdN = await editorHasFocus();
  await p().keyboard.press('Meta+n');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="new"]', { timeout: 5_000 }).catch(() => undefined);
  const viewAfterCmdN = await stageView();
  record('fix boundary exposed', { world, control0, onBody, onToggle, tabsToEditor: tabs, arrows, stillInEditor, tabLeftEditor, shiftTabBack, viewBeforeEscape, viewAfterEscape, editorBeforeCmdN, viewAfterCmdN });
  assert.equal(world.largeWorld && !world.mapDrawn, true, `STATE: the exposed state (${world.cols} x ${world.rows}, the Map closed)`);
  assert.equal(control0.followStayed, true, 'CONTROL W0: with no key, the follow stays');
  assert.equal(onBody.focusIsBody && onToggle.focusIs, true, 'SETUP: the outside focuses held');
  assert.equal(onBody.panListenerRan, true, 'CONSTRAINT 2: with focus on the page body (outside any scroller), the pan RUNS');
  assert.equal(onToggle.panListenerRan, true, 'CONSTRAINT 2: with focus on a button outside the editor, the pan RUNS');
  for (const [key, r] of Object.entries(arrows)) assert.equal(r.panListenerRan, false, `CONSTRAINT 1: ${key} inside the editor does not reach the pan`);
  assert.equal(stillInEditor, true, 'the arrows left focus in the editor');
  assert.equal(tabLeftEditor && shiftTabBack, true, 'CONSTRAINT 1: Tab and Shift+Tab still move focus out of and back into the editor');
  assert.equal(viewBeforeEscape, 'diff', 'SETUP: the Code Diff is open');
  assert.equal(viewAfterEscape, 'mission', 'CONSTRAINT 1: Escape from inside the editor still peels ONE layer (diff -> mission)');
  assert.equal(editorBeforeCmdN, true, 'SETUP: the editor holds focus before Cmd+N');
  assert.equal(viewAfterCmdN, 'new', 'CONSTRAINT 1: Cmd+N from inside the editor still opens New Mission');
}, STEPS_MS);

// ---- CASE 2: the code review dialog's code pane (the sibling surface; diCodeReview.test.ts:763-766's claim) ----
for (const state of [
  { world: 'large' as World, mapOpen: false, label: 'THE EXPOSED STATE: the metropolis, Map CLOSED' },
  { world: 'small' as World, mapOpen: false, label: "CONTRAST: the mock's small world, Map closed (diCodeReview's recorded state)" },
  { world: 'large' as World, mapOpen: true, label: 'CONTRAST: the metropolis, Map OPEN' },
])
  test(`CASE 2, the review dialog's code pane: ${state.label}`, async () => {
    await bootOn('panel', state.world, { width: 1600, height: 1000 });
    await openReviewLongLine(state.mapOpen);
    const world = await worldState();
    const control0 = await w0();
    // CONTROL: a non-form-control focus INSIDE the dialog (the file row, a button): does the listener run through the dialog?
    await p().locator('[data-testid="di-review-row"][data-path="web/long_line.js"]').focus();
    const onRow = await observe('[data-testid="di-review-row"][data-path="web/long_line.js"]', REVIEW, 1);
    // CONTROL: the dialog's search input: the listener must SKIP it
    const hasSearch = (await p().locator('[data-testid="di-review-search"]').count()) > 0;
    let onInput: Awaited<ReturnType<typeof observe>> | null = null;
    if (hasSearch) {
      await p().locator('[data-testid="di-review-search"]').focus();
      onInput = await observe('[data-testid="di-review-search"]', REVIEW, 1);
    }
    // THE CLAIM: a sequential route from the file row to the code pane, then ArrowRight on it
    await p().locator('[data-testid="di-review-row"][data-path="web/long_line.js"]').focus();
    const tabs = await tabTo(REVIEW, 30);
    const onPane = await observe(REVIEW, REVIEW, 6);
    await shot(`b2da-case2-${state.world}-map-${state.mapOpen ? 'open' : 'closed'}-after-arrows.png`);
    record(`case 2 ${state.world} map ${state.mapOpen ? 'open' : 'closed'}`, { world, control0, outsideDialog: outside, onRow, onInput, tabsToPane: tabs, onPane });
    assert.equal(world.largeWorld, state.world === 'large', `STATE: the world is ${state.world} (${world.cols} x ${world.rows})`);
    assert.equal(world.mapDrawn, state.mapOpen, `STATE: the Map is ${state.mapOpen ? 'open' : 'closed'}`);
    assert.equal(control0.followStayed, true, 'CONTROL W0: with no key, the follow stays');
    if (onInput !== null) assert.equal(onInput.panListenerRan, false, 'CONTROL: on the search input the listener SKIPS');
    assert.equal(onPane.focusIs, true, 'the code pane holds focus (reached by sequential Tab)');
    assert.equal(outside?.focusIs, true, 'SETUP: the outside control held focus');
    if (state.world === 'large' && !state.mapOpen)
      assert.equal(outside?.panListenerRan, true, 'POSITIVE CONTROL: OUTSIDE the dialog, in THIS state, the listener RUNS (so it is installed here)');
  }, STEPS_MS);

// ---- THE TOGGLE's take on the PATH (the Lead's point 4): the bar, with the toggle and (PROBE) without it ----
for (const vp of [
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`TOGGLE vs PATH at ${vp.width}x${vp.height}, the tree OPEN (the default): what the control takes from the editor bar, in px`, async () => {
    await boot(vp);
    await openCodeDiff();
    await selectFile('syntax_sample.ts');
    const bar = () =>
      p().evaluate(() => {
        const t = document.querySelector<HTMLElement>('[data-testid="di-scan-files-toggle"]');
        const path = document.querySelector<HTMLElement>('[data-testid="di-scan-editor-path"]')!;
        const code = document.querySelector<HTMLElement>('.di-scan-diff-line .di-scan-code')!;
        const cs = getComputedStyle(code);
        return {
          toggleWidthPx: t && t.checkVisibility() ? Number(t.getBoundingClientRect().width.toFixed(1)) : 0,
          pathVisiblePx: path.clientWidth,
          pathNeedsPx: path.scrollWidth,
          pathCut: path.scrollWidth > path.clientWidth + 1,
          codeChannelPx: Math.round(code.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)),
        };
      });
    const withToggle = await bar();
    await p().evaluate(() => document.querySelector<HTMLElement>('[data-testid="di-scan-files-toggle"]')!.style.setProperty('display', 'none'));
    await p().waitForTimeout(100);
    const PROBE_withoutToggle = await bar();
    await p().evaluate(() => document.querySelector<HTMLElement>('[data-testid="di-scan-files-toggle"]')!.style.removeProperty('display'));
    record(`toggle vs path ${vp.width}x${vp.height}`, { withToggle, PROBE_withoutToggle, pathPxTakenByToggle: PROBE_withoutToggle.pathVisiblePx - withToggle.pathVisiblePx, codeChannelPxTakenByToggle: PROBE_withoutToggle.codeChannelPx - withToggle.codeChannelPx });
    assert.ok(withToggle.toggleWidthPx > 0 && PROBE_withoutToggle.toggleWidthPx === 0, 'INSTRUMENT: the toggle is drawn, then (PROBE) not drawn');
  }, STEPS_MS);
