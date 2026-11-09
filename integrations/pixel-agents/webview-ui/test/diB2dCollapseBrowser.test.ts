/**
 * B2d, RENDERED in Chrome (headless; not Brave evidence): the Code Diff's file-tree COLLAPSE, the named font delta, #4 re-keyed, against the Supervisor's
 * behavioural bar (`.herd/state/b2d-code-readability-ruling.md`) and the Lead's rulings. The real App on the scan face (test/harness/di-app-scan.html),
 * fed the REAL review answers over a fixture git workspace (test/fixtures/di-office-state-code-review-v2.FIXTURE.json). Nothing here is live.
 *
 * Each claim names the assertion that carries it (method rule 2), every outcome read AFTER the action (rule 7), each instrument at the level of its claim
 * (rule 6), every element named (rule 6c). RULE 7 FIRST: the default with no choice made, the [hidden] term (with a positive control that removes it), the
 * state remembered across navigation and across resize. Then the keyboard (sequential Tab to the control, Enter / Space, focus kept, no layer dismissed),
 * the Escape contract, and LONG LINES REACHED BY KEYBOARD as a rendered path. The CANDIDATE is measured at BOTH shipping sizes (1440x900, 1600x1000:
 * asserted, BOUNDED to this sample, this face, these viewports) and the diagnostic widths (recorded). MONOTONIC: the code channel never shrinks as the
 * window grows, across the full docked width set and through the tree's ramp; one pane is checked on its own; the pane switch (1439 -> 1440, where the list
 * docks, by design) is recorded, not claimed monotone. Character counts are COMPUTED for THIS machine's drawn face, with the advance stated; px is the
 * product property. The installed-JetBrains-Mono case is NOT measured.
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
import { clippedWords } from '../src/components/diCodeReviewModel.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B2DC_SHOTS ?? null;


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
    console.warn(`SKIPPED (B2d collapse): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B2DC ${name}: ${JSON.stringify(r)}`);

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
const focusNow = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);
const viewNow = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);

/** The collapse as drawn: the control (name, aria), the tree (drawn or not, and why), the list beside. */
const collapse = () =>
  p().evaluate(() => {
    const t = document.querySelector<HTMLElement>('[data-testid="di-scan-files-toggle"]');
    const tree = document.getElementById('di-scan-files');
    const list = document.querySelector<HTMLElement>('[data-testid="di-missions"]');
    const tb = tree?.getBoundingClientRect();
    return {
      control: t ? { text: (t.textContent ?? '').trim(), expanded: t.getAttribute('aria-expanded'), controls: t.getAttribute('aria-controls'), drawn: t.checkVisibility({ checkVisibilityCSS: true }) } : null,
      tree: tree ? { hiddenAttr: tree.hasAttribute('hidden'), display: getComputedStyle(tree).display, drawn: tree.checkVisibility({ checkVisibilityCSS: true }), width: Math.round(tb?.width ?? 0) } : null,
      listDrawn: list ? { width: Math.round(list.getBoundingClientRect().width), visible: list.checkVisibility({ checkVisibilityCSS: true }) } : null,
      panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
    };
  });

/** The code channel at the DRAWN code cell, the drawn face and the path in the editor bar (each read where it is drawn). */
const channel = () =>
  p().evaluate(() => {
    const code = document.querySelector<HTMLElement>('.di-scan-diff-line .di-scan-code');
    if (code === null) throw new Error('no code cell is drawn');
    const cs = getComputedStyle(code);
    const px = code.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
    probe.textContent = 'M'.repeat(200);
    code.append(probe);
    const advance = probe.getBoundingClientRect().width / 200;
    probe.remove();
    const width = (family: string) => {
      const s = document.createElement('span');
      s.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-size:12px;font-family:${family}`;
      s.textContent = 'MMMMMMMMMMiiiiiiiiii0123456789';
      document.body.append(s);
      const w = s.getBoundingClientRect().width;
      s.remove();
      return w;
    };
    const cells = [...document.querySelectorAll<HTMLElement>('.di-scan-diff-line .di-scan-code')];
    const path = document.querySelector<HTMLElement>('[data-testid="di-scan-editor-path"]');
    const tree = document.querySelector<HTMLElement>('.di-scan-tree');
    return {
      channelPx: Math.round(px),
      treeWidthPx: tree && tree.checkVisibility() ? Number(tree.getBoundingClientRect().width.toFixed(1)) : 0,
      stageWidthPx: Math.round(document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]')?.getBoundingClientRect().width ?? 0),
      consumedStackDECLARED: cs.fontFamily,
      codeSize: cs.fontSize,
      advanceAtDrawnCellPx: Number(advance.toFixed(3)),
      charsCOMPUTEDthisMachine: Math.floor(px / advance),
      jetbrainsMonoDrawn: Math.abs(width('"JetBrains Mono", serif') - width('serif')) > 0.5,
      lines: cells.length,
      overflowing: cells.filter((c) => c.scrollWidth > c.clientWidth + 1).length,
      pathCut: path ? path.scrollWidth > path.clientWidth + 1 : null,
    };
  });

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

// ---- RULE 7 FIRST: the default when no choice has been made, and the defensive [hidden] term ----

for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`RULE 7, THE DEFAULT at ${vp.width}x${vp.height}, no choice made: the tree is OPEN and the control is labelled "Hide files"`, async () => {
    await boot(vp);
    await openCodeDiff();
    const c = await collapse();
    record(`default ${vp.width}`, c);
    assert.deepEqual(c.control, { text: 'Hide files', expanded: 'true', controls: 'di-scan-files', drawn: true }, 'the labelled control, expanded');
    assert.equal(c.tree?.drawn, true, 'the tree is drawn by default (open: the file navigation is never hidden on the default path)');
    assert.equal(c.panes, vp.width >= 1440 ? 'two' : 'one', 'STATE: the panes the window names');
    if (vp.width >= 1440) assert.ok(c.listDrawn !== null && c.listDrawn.visible && c.listDrawn.width > 0, 'the persistent list is drawn beside the diff by default');
  }, STEPS_MS);

// RULE 7, CORRECTED (collapse run 1): this test first asserted that a DEFENSIVE `.di-scan-tree[hidden] { display: none }` rule was what hid the tree, with a
// control that re-asserted `display: flex !important`. The control could not un-hide it: Tailwind's preflight (`[hidden]... { display: none !important }`
// in its BASE LAYER) wins over any unlayered author `!important`. So the defensive rule was REDUNDANT and its premise false. It is removed, and the
// control moves to the level of the mechanism: the `hidden` ATTRIBUTE.
test('RULE 7, THE hidden MECHANISM: collapsed, the tree is NOT DRAWN, and it is the hidden ATTRIBUTE that hides it (a positive control removes it)', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await p().click(TOGGLE);
  const collapsed = await collapse();
  // RECORDED, the cascade fact: an unlayered author `display: flex !important` does NOT un-hide it (the layered preflight `!important` wins)
  await p().evaluate(() => {
    const s = document.createElement('style');
    s.id = 'b2dc-author';
    s.textContent = '.di-scan-tree[hidden] { display: flex !important; }';
    document.head.append(s);
  });
  const underAuthorImportant = await collapse();
  await p().evaluate(() => document.getElementById('b2dc-author')?.remove());
  // POSITIVE CONTROL, at the mechanism's level: the hidden ATTRIBUTE removed (measurement only, restored), the tree IS drawn
  await p().evaluate(() => document.getElementById('di-scan-files')!.removeAttribute('hidden'));
  const withoutAttribute = await collapse();
  await p().evaluate(() => document.getElementById('di-scan-files')!.setAttribute('hidden', ''));
  const restored = await collapse();
  record('hidden mechanism', { collapsed, underAuthorImportant, withoutAttribute, restored });
  assert.deepEqual(collapsed.tree, { hiddenAttr: true, display: 'none', drawn: false, width: 0 }, 'collapsed: the tree is not drawn');
  assert.equal(collapsed.control?.expanded, 'false', 'the control says collapsed');
  assert.equal(collapsed.control?.text, 'Show files', 'and is labelled to show the files again');
  assert.equal(withoutAttribute.tree?.drawn, true, 'POSITIVE CONTROL: without the hidden attribute the tree IS drawn (the attribute is the mechanism)');
  assert.equal(restored.tree?.drawn, false, 'restored: not drawn again');
}, STEPS_MS);

test('RULE 7, REMEMBERED ACROSS NAVIGATION: collapsed, leave Code Diff and come back: still collapsed; expanded, the same', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await p().click(TOGGLE);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector(TOGGLE);
  const back = await collapse();
  await p().click(TOGGLE);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector(TOGGLE);
  const backOpen = await collapse();
  record('remembered across navigation', { back, backOpen });
  assert.equal(back.control?.expanded, 'false', 'collapsed, after a navigation away and back: still collapsed');
  assert.equal(back.tree?.drawn, false, 'and the tree is still not drawn');
  assert.equal(backOpen.control?.expanded, 'true', 'expanded, after a navigation away and back: still expanded');
  assert.equal(backOpen.tree?.drawn, true, 'and the tree is drawn');
}, STEPS_MS);

test('RULE 7, REMEMBERED ACROSS RESIZE: collapsed at 1440, the window to 1280 (one pane) and 1600: still collapsed; expanded, back to 1440: still expanded', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await p().click(TOGGLE);
  const seen: Record<string, unknown> = {};
  for (const vp of [
    { width: 1280, height: 800 },
    { width: 1600, height: 1000 },
  ]) {
    await p().setViewportSize(vp);
    await p().waitForTimeout(500);
    seen[`collapsed@${vp.width}`] = await collapse();
  }
  await p().click(TOGGLE);
  await p().setViewportSize({ width: 1440, height: 900 });
  await p().waitForTimeout(500);
  seen['expanded@1440'] = await collapse();
  record('remembered across resize', seen);
  for (const k of ['collapsed@1280', 'collapsed@1600']) {
    const c = seen[k] as Awaited<ReturnType<typeof collapse>>;
    assert.equal(c.control?.expanded, 'false', `${k}: still collapsed`);
    assert.equal(c.tree?.drawn, false, `${k}: the tree still not drawn`);
  }
  const e = seen['expanded@1440'] as Awaited<ReturnType<typeof collapse>>;
  assert.equal(e.control?.expanded, 'true', 'expanded, then resized: still expanded');
  assert.equal(e.tree?.drawn, true, 'and the tree drawn');
}, STEPS_MS);

// ---- the keyboard and the Escape contract ----

test('KEYBOARD: the control is reached by sequential Tab; Enter collapses and Space expands; focus stays on it; no layer is dismissed', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  const start = await focusNow();
  const presses = await tabTo(TOGGLE);
  await p().keyboard.press('Enter');
  const afterEnter = { ...(await collapse()), focus: await focusNow(), view: await viewNow() };
  await p().keyboard.press('Space');
  const afterSpace = { ...(await collapse()), focus: await focusNow(), view: await viewNow() };
  record('keyboard', { start, presses, afterEnter, afterSpace });
  assert.equal(afterEnter.control?.expanded, 'false', 'Enter collapsed the tree');
  assert.equal(afterEnter.tree?.drawn, false, 'the tree is not drawn');
  assert.equal(afterEnter.focus, 'di-scan-files-toggle', 'focus stays on the control (it lives outside the tree)');
  assert.equal(afterEnter.view, 'diff', 'no layer was dismissed: still Code Diff');
  assert.equal(afterSpace.control?.expanded, 'true', 'Space expanded it again (reversible)');
  assert.equal(afterSpace.focus, 'di-scan-files-toggle', 'focus still on the control');
  assert.ok(afterSpace.listDrawn !== null && afterSpace.listDrawn.visible, 'the list stays beside throughout');
}, STEPS_MS);

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

test('FIND WITH FILES COLLAPSED (round 20): "Find a file" OPENS the tree it searches and its box; the control reads pressed exactly when the box is shown; the query filters the tree', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await p().click(TOGGLE);
  await p().waitForFunction(() => document.querySelector<HTMLElement>('.di-scan-tree')?.checkVisibility() === false);
  // B3 1B: the Find control is an item of the More menu, which exists only while the menu is open: its pressed state is READ by opening the menu by real keys, reading the item, and closing with Escape (the menu consumes it)
  const entrance: number[] = [];
  const readPressed = async () => {
    entrance.push(await openMoreByKeys());
    const pressed = await p().getAttribute('[data-testid="di-scan-diff-find"]', 'aria-pressed');
    await p().keyboard.press('Escape');
    return pressed;
  };
  const find = async () => ({
    ...(await p().evaluate(() => ({
      treeDrawn: document.querySelector<HTMLElement>('.di-scan-tree')?.checkVisibility() ?? false,
      boxDrawn: document.querySelector<HTMLElement>('[data-testid="di-scan-diff-search"]')?.checkVisibility() ?? false,
      expanded: document.querySelector('[data-testid="di-scan-files-toggle"]')?.getAttribute('aria-expanded') ?? null,
    }))),
    pressed: await readPressed(),
  });
  const collapsed = await find();
  entrance.push(await activateFindByKeys());
  await p().waitForTimeout(150);
  const afterFind = await find();
  let filtered: string[] = [];
  if (afterFind.boxDrawn) {
    await p().fill('[data-testid="di-scan-diff-search"]', 'long_line');
    await p().waitForTimeout(150);
    filtered = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-tree-file"]')].filter((b) => b.checkVisibility()).map((b) => b.textContent ?? ''),
    );
  }
  await p().click(TOGGLE);
  await p().waitForTimeout(150);
  const hiddenAgain = await find();
  // THE BRANCH THE FIX EXISTS FOR (the Reviewer's pre-read): Find still on, the files hidden. A second activation must OPEN, never toggle off: the tree
  // and the box reopen, the QUERY is retained, and a real FILTERED result is shown (a flipped attribute over an empty box would pass weaker checks).
  entrance.push(await activateFindByKeys());
  await p().waitForTimeout(150);
  const reopened = await find();
  const reopenedQuery = reopened.boxDrawn ? await p().inputValue('[data-testid="di-scan-diff-search"]') : null;
  const refiltered = await p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-tree-file"]')].filter((b) => b.checkVisibility()).map((b) => b.textContent ?? ''),
  );
  record('find with files collapsed', { collapsed, afterFind, filtered, hiddenAgain, reopened, reopenedQuery, refiltered, entranceKeyPresses: entrance });
  assert.equal(collapsed.treeDrawn, false, 'STATE: the files are collapsed before Find');
  assert.equal(afterFind.treeDrawn, true, 'CONTRACT: Find with the files collapsed OPENS the tree it searches');
  assert.equal(afterFind.boxDrawn, true, 'CONTRACT: and its find box is shown');
  assert.equal(afterFind.pressed, 'true', 'CONTRACT: the control reads pressed');
  assert.equal(afterFind.expanded, 'true', 'CONTRACT: the files control reads expanded');
  assert.ok(filtered.length >= 1 && filtered.every((t) => t.includes('long_line')), `CONTRACT: the query filters the tree (${filtered.join(' | ')})`);
  assert.equal(hiddenAgain.pressed, 'false', 'CONTRACT: hiding the files again, the Find control no longer reads pressed (the box is not shown)');
  assert.equal(hiddenAgain.treeDrawn, false, 'STATE: the files are hidden again, with Find still on');
  assert.equal(reopened.treeDrawn, true, 'CONTRACT (second activation, Find on and the files hidden): Find OPENS the tree again, it does not toggle off');
  assert.equal(reopened.boxDrawn, true, 'CONTRACT (second activation): the find box is shown again');
  assert.equal(reopened.pressed, 'true', 'CONTRACT (second activation): the control reads pressed again');
  assert.equal(reopenedQuery, 'long_line', 'CONTRACT (second activation): the query is RETAINED');
  assert.ok(refiltered.length >= 1 && refiltered.every((t) => t.includes('long_line')), `CONTRACT (second activation): a real FILTERED result is shown (${refiltered.join(' | ')})`);
}, STEPS_MS);

test('ESCAPE CONTRACT: with focus on the control, Escape peels Code Diff to Mission details (one layer, as before); the collapse state is kept', async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await p().locator(TOGGLE).focus();
  await p().keyboard.press('Enter');
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  const view = await viewNow();
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector(TOGGLE);
  const kept = await collapse();
  record('escape contract', { view, kept });
  assert.equal(view, 'mission', 'Escape peeled exactly one layer: Code Diff to Mission details');
  assert.equal(kept.control?.expanded, 'false', 'the collapse survived the Escape and the return');
}, STEPS_MS);

// ---- long lines: content and indentation preserved, and reachable BY KEYBOARD (a rendered path, not an overflow property) ----

// Chrome scrolls a focused scroller VERTICALLY on Home and End, so the horizontal keyboard path is the arrow keys: ArrowRight, held or repeated, to the
// line's end, and ArrowLeft back. The presses are counted, and both ends are asserted reached.
/** The long line as the FIXTURE holds it: its RETAINED source text (its length read, never assumed) and the upstream clip count. The title once said
 * 1237, a document-wide maximum of drawn cells. The drawn cell is in fact sign (1) + retained source (1200) + the clip notice (36), because
 * diScanDiffRead.ts folds the truthful upstream clip into the drawn text; the notice is built here with the product's own clippedWords.
 * SCOPE: the claim is SOURCE PRESERVATION, so the 1200 characters are asserted character for character and the clip COUNT comes from the fixture,
 * while the notice FORMAT is single-sourced from the product (a legitimate rewording is a different concern).
 * ITS LIMIT: this does NOT verify the notice's WORDING. It verifies that what follows the source is the product's own notice built from the fixture's
 * clipped count; if the notice itself were wrong, this test would not say so. */
const LONG = (doc.replies.files['web/long_line.js'] as unknown as { file: { hunks: Array<{ lines: Array<{ kind: string; text: string; clipped?: number }> }> } }).file.hunks
  .flatMap((h) => h.lines)
  .reduce((a, b) => (b.text.length > a.text.length ? b : a));
const LONG_LINE = LONG.text;
const CLIP_NOTICE = (LONG.clipped ?? 0) > 0 ? ` … (${clippedWords(LONG.clipped ?? 0)})` : '';

test(`LONG LINES, KEYBOARD: the editor is reached by sequential Tab from the control; web/long_line.js's ${LONG_LINE.length} retained source characters (read from the fixture) are drawn WHOLE in ONE cell, the diff sign and the clip notice verified as METADATA; ArrowRight reaches its END; ArrowLeft returns to its start`, async () => {
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await selectFile('syntax_sample.ts');
  const indented = await p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('.di-scan-diff-line .di-scan-code')].filter((c) => /^ {2,}\S/.test(c.textContent ?? '')).length,
  );
  await selectFile('long_line.js');
  const longest = await p().evaluate(() => Math.max(...[...document.querySelectorAll<HTMLElement>('.di-scan-diff-line .di-scan-code')].map((c) => (c.textContent ?? '').length)));
  // THE CLAIM'S OWN INSTRUMENT: the SELECTED file's cells in the editor (not a document-wide maximum); the one cell holding the line, its sign element
  // read apart from its text
  const cells = await p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-editor"] .di-scan-diff-line .di-scan-code')].map((c) => ({
      sign: c.querySelector('.di-scan-sign')?.textContent ?? '',
      text: c.textContent ?? '',
    })),
  );
  const holding = cells.filter((c) => c.text.includes(LONG_LINE));
  const cell = holding[0] ?? null;
  const afterSign = cell ? cell.text.slice(cell.sign.length) : null;
  await p().locator(TOGGLE).focus();
  const presses = await tabTo('[data-testid="di-scan-editor"]', 40);
  const scroll = () =>
    p().evaluate(() => {
      const b = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!;
      return { left: Math.round(b.scrollLeft), max: b.scrollWidth - b.clientWidth };
    });
  const before = await scroll();
  await press('ArrowRight', 5);
  await p().waitForTimeout(150);
  const afterRight = await scroll();
  let rightPresses = 5;
  for (let s = await scroll(); s.left < s.max - 1 && rightPresses < 600; s = await scroll()) {
    await press('ArrowRight', 10);
    rightPresses += 10;
  }
  await p().waitForTimeout(150);
  const atEnd = await scroll();
  let leftPresses = 0;
  for (let s = await scroll(); s.left > 0 && leftPresses < 600; s = await scroll()) {
    await press('ArrowLeft', 10);
    leftPresses += 10;
  }
  await p().waitForTimeout(150);
  const atStart = await scroll();
  const focusStayed = await focusNow();
  record('long lines keyboard', { indentedLines: indented, retainedSourceChars: LONG_LINE.length, clipped: LONG.clipped ?? 0, clipNoticeChars: CLIP_NOTICE.length, cellsHoldingTheLine: holding.length, signChars: cell?.sign.length ?? null, drawnCellChars: cell?.text.length ?? null, longestCharsDocumentWide: longest, tabsFromControlToEditor: presses, before, afterRight, atEnd, rightPresses, atStart, leftPresses, focusStayed });
  assert.ok(indented >= 1, 'indentation is preserved in the drawn code (white-space: pre)');
  assert.ok(LONG_LINE.length >= 1000, `SETUP: the fixture's long line is long (${LONG_LINE.length} retained source characters)`);
  assert.equal(holding.length, 1, 'the line is drawn in ONE cell of the selected file');
  assert.equal(cell?.sign, LONG.kind === 'add' ? '+' : '-', 'METADATA: the diff sign is its own element, not source');
  assert.equal(afterSign?.slice(0, LONG_LINE.length), LONG_LINE, `the ${LONG_LINE.length} retained source characters are drawn EXACTLY, in order (content preserved, not truncated)`);
  assert.equal(afterSign?.slice(LONG_LINE.length), CLIP_NOTICE, `METADATA: what follows the source is exactly the truthful clip notice (${CLIP_NOTICE.length} characters), nothing else`);
  assert.ok(before.max > 0, 'it overflows the channel: overflow that SCROLLS');
  assert.ok(afterRight.left > before.left, 'ArrowRight scrolls it sideways');
  assert.ok(Math.abs(atEnd.left - atEnd.max) <= 1, `ArrowRight reaches the END of the line (${rightPresses} presses): the whole line is reachable by keyboard`);
  assert.equal(atStart.left, 0, `ArrowLeft returns to its start (${leftPresses} presses)`);
  assert.equal(focusStayed, 'di-scan-editor', 'focus stayed in the editor throughout');
}, STEPS_MS);

// ---- the CANDIDATE, measured: both shipping sizes (asserted, bounded) and the diagnostic widths (recorded) ----

for (const vp of [
  { width: 1440, height: 900, shipping: true },
  { width: 1600, height: 1000, shipping: true },
  { width: 1280, height: 800, shipping: false },
  { width: 1480, height: 900, shipping: false },
  { width: 1500, height: 941, shipping: false },
  { width: 1672, height: 941, shipping: false },
])
  test(`CANDIDATE at ${vp.width}x${vp.height}${vp.shipping ? ' (SHIPPING)' : ' (diagnostic)'}: the real collapse, its control present; the bounded bar`, async () => {
    await boot(vp);
    await openCodeDiff();
    await selectFile('syntax_sample.ts');
    const open = { ...(await channel()), collapse: await collapse() };
    await shot(`b2dc-${vp.width}x${vp.height}-syntax_sample-OPEN.png`);
    await p().click(TOGGLE);
    await p().waitForTimeout(150);
    const collapsed = { ...(await channel()), collapse: await collapse() };
    await shot(`b2dc-${vp.width}x${vp.height}-syntax_sample-COLLAPSED.png`);
    record(`candidate ${vp.width}x${vp.height}`, { open, collapsed, browser: 'Chrome 156.0.8078.80 headless' });
    assert.equal(open.codeSize, '12px', 'code keeps its size');
    assert.equal(collapsed.codeSize, '12px', 'code keeps its size, collapsed too');
    assert.ok(!open.consumedStackDECLARED.includes('JetBrains'), 'the editor consumes the --di-font-code stack (the named font delta)');
    if (vp.shipping) {
      assert.equal(collapsed.overflowing, 0, `BOUNDED BAR at ${vp.width}x${vp.height}: collapsed, no line of the sample overflows (this sample, this face)`);
      assert.ok(collapsed.collapse.listDrawn !== null && collapsed.collapse.listDrawn.visible, 'the list stays beside, collapsed too');
      assert.equal(collapsed.pathCut, false, 'collapsed, the selected file\'s path is drawn whole (the selection stays discoverable)');
    }
  }, STEPS_MS);

// ---- #4: MONOTONIC across the FULL width set (two panes, through the ramp); one pane recorded, and the pane switch named ----

test('MONOTONIC: the code channel never shrinks as the window grows, open and collapsed, across the full docked width set and through the ramp', async () => {
  const docked = [1440, 1480, 1500, 1600, 1642, 1643, 1644, 1650, 1657, 1658, 1672, 1920];
  const onePane = [1280, 1400, 1439];
  await boot({ width: 1440, height: 900 });
  await openCodeDiff();
  await selectFile('syntax_sample.ts');
  const read = async (w: number) => {
    await p().setViewportSize({ width: w, height: 941 });
    await p().waitForTimeout(350);
    return { width: w, ...(await channel()) };
  };
  const openRows = [];
  for (const w of [...onePane, ...docked]) openRows.push(await read(w));
  await p().click(TOGGLE);
  const collapsedRows = [];
  for (const w of [...onePane, ...docked]) collapsedRows.push(await read(w));
  const compact = (r: { width: number; channelPx: number; treeWidthPx: number; stageWidthPx: number }) => ({ w: r.width, stage: r.stageWidthPx, tree: r.treeWidthPx, ch: r.channelPx });
  record('monotonic', { open: openRows.map(compact), collapsed: collapsedRows.map(compact) });
  const check = (rows: Array<{ width: number; channelPx: number }>, label: string) => {
    for (let i = 1; i < rows.length; i++)
      assert.ok(rows[i].channelPx >= rows[i - 1].channelPx, `${label}: ${rows[i - 1].width} -> ${rows[i].width}: the channel ${rows[i - 1].channelPx} -> ${rows[i].channelPx} must not shrink`);
  };
  const dockedOpen = openRows.filter((r) => r.width >= 1440);
  const dockedCollapsed = collapsedRows.filter((r) => r.width >= 1440);
  check(dockedOpen, 'docked, tree open');
  check(dockedCollapsed, 'docked, collapsed');
  check(openRows.filter((r) => r.width < 1440), 'one pane, tree open');
  check(collapsedRows.filter((r) => r.width < 1440), 'one pane, collapsed');
  // the ramp itself, read where it is drawn: 340 up to a 1015 px stage, 354 from 1029
  assert.equal(dockedOpen.find((r) => r.width === 1600)?.treeWidthPx, 340, 'below the threshold (1600: a 972 px stage) the docked tree is 340');
  assert.equal(dockedOpen.find((r) => r.width === 1672)?.treeWidthPx, 354, 'past the ramp (1672: a 1044 px stage) it is the reference 354');
}, STEPS_MS);
