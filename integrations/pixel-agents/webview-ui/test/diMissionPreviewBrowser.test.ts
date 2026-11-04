/**
 * Deliverable 3: the ISOLATED Mission preview (test/harness/di-mission-preview.config.ts), driven in a real browser through its OWN launch path: the REAL
 * app over the preview's fixture transport. FIXTURE / VISUAL TEST ONLY; nothing is live.
 *
 * It proves what the preview is for: it binds no route and grants no authority; its scenes reach every one of the 15 statuses, and only those; ONE Mission reads the
 * same on the Missions table, its details, Reception and the rail; a finished Mission leaves Mine and every filter but Archived at once; an archived Mission's
 * actions are drawn disabled with their reasons visible; the owner's hold-to-delete shows each recorded answer where it was asked; the path is reachable by keyboard;
 * and the narrow viewport is measured (what fits, and what is reachable only by scrolling the stage), never assumed.
 *
 * It runs the launch path itself: `vite build` of the preview config (into this test's own temporary directory) and `vite preview` of that build. Every
 * page it opens is held to a CLEAN console: no error and no warning, so a real one is never hidden among expected ones.
 *
 * Captures: with DI_PREVIEW_CAPTURE_DIR=<absolute dir>, each named view is written there as a PNG (desktop 1440 x 900, narrow 414 x 896 and 375 x 812),
 * with capture-report.json (scene, fixture and its sha256, viewport, the PNG's sha256 and what was measured). Without it, nothing is written.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an explicit message.
 */
import assert from 'node:assert/strict';
import { type ChildProcess, spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';

import { MR_DELETE_PENDING_WORDS, MR_DELETE_RECLAIMED_WORDS } from '../src/components/diMissionRequestModel.ts';
import { DI_MISSION_STATUS, DI_MISSION_STATUS_IDS, type DiMissionStatusId } from '../src/components/diMissionStatus.ts';
import { DI_DELIVERY_ARCHIVED_ACT } from '../src/components/diScanModel.ts';
import { DI_DELETE_HOLD_MS } from '../src/constants.ts';
import {
  DELETE_REF,
  DI_PREVIEW_SCENES,
  type DiPreviewTraffic,
  LIFECYCLE_REF,
  OVERDUE_REF,
  PREVIEW_BUILD_COMMAND,
  PREVIEW_INDEX,
  PREVIEW_PORT,
  PREVIEW_SCENE_PAGE,
  PREVIEW_SERVE_COMMAND,
  PREVIEW_TRAFFIC,
  RUNNING_REF,
  sceneState,
} from './harness/diMissionPreview.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = path.join(root, '..', 'docs', 'di-mission-preview.md');
const CAPTURE_DIR = process.env.DI_PREVIEW_CAPTURE_DIR;
if (CAPTURE_DIR !== undefined && !path.isAbsolute(CAPTURE_DIR)) throw new Error('DI_PREVIEW_CAPTURE_DIR must be an absolute directory');
const DESKTOP = { width: 1440, height: 900 } as const;
const NARROWS = [
  { width: 414, height: 896 },
  { width: 375, height: 812 },
] as const;
type Viewport = { width: number; height: number };
const LABEL_OF = new Map(DI_MISSION_STATUS_IDS.map((id) => [DI_MISSION_STATUS[id].label, id] as const));
const ARCHIVED_READ_ONLY = 'Archived: this Mission is read-only. Nothing can be started, restarted, run or delivered from it.';
const NON_ARCHIVED_FILTERS = ['all', 'needs', 'stalled', 'running', 'done'] as const;
const LINKED_DELIVERY = sceneState(DI_PREVIEW_SCENES.find((s) => s.id === 'archived-delivery')!).missions.missions.find(
  (m) => m.v2.deliveryLink?.state === 'linked',
)!.missionRef;

let server: ChildProcess | null = null;
let serverOutput = '';
/** This test's own build directory (created here, removed here). */
let outDir = '';
/** Every console error or warning, every HTTP error answer and every request that failed outright, of every page this test opened. */
const noise: string[] = [];
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
const report: Array<Record<string, unknown>> = [];

beforeAll(async () => {
  // THE launch path, the very commands the docs give, run from webview-ui: only the port (a free one) and the build directory (this test's own) differ
  outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'di-mission-preview-'));
  // a reader's shell, not this runner's: vitest sets NODE_ENV=test (and its own VITEST_* variables), which the preview build refuses
  const env: NodeJS.ProcessEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => k !== 'NODE_ENV' && !k.startsWith('VITEST')));
  env.DI_PREVIEW_OUT_DIR = outDir;
  const built = await run(PREVIEW_BUILD_COMMAND, env);
  assert.equal(built.code, 0, `the preview build failed:\n${built.output}`);
  const port = await freePort();
  const serve = PREVIEW_SERVE_COMMAND.map((arg) => (arg === String(PREVIEW_PORT) ? String(port) : arg));
  server = spawn(serve[0]!, serve.slice(1), { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout?.on('data', (c: Buffer) => (serverOutput += c.toString()));
  server.stderr?.on('data', (c: Buffer) => (serverOutput += c.toString()));
  origin = `http://localhost:${port}`;
  const until = Date.now() + 60_000;
  while ((await fetch(`${origin}${PREVIEW_INDEX}`).then((r) => r.status, () => 0)) !== 200) {
    if (Date.now() > until || server.exitCode !== null) throw new Error(`the preview did not start:\n${serverOutput}`);
    await new Promise((r) => setTimeout(r, 250));
  }
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
  if (CAPTURE_DIR !== undefined) fs.mkdirSync(CAPTURE_DIR, { recursive: true });
}, STEPS_MS);

afterAll(async () => {
  await page?.close();
  await browser?.close();
  if (server !== null && server.exitCode === null) {
    const exited = new Promise((r) => server!.once('exit', r));
    server.kill('SIGTERM');
    await Promise.race([exited, new Promise((r) => setTimeout(r, 10_000))]);
  }
  if (outDir !== '') fs.rmSync(outDir, { recursive: true, force: true });
  if (CAPTURE_DIR !== undefined && report.length > 0) {
    // every fixture a scene reads: its office state, and the recorded delete replies the delete scenes answer with
    const fixtures = [...new Set([...DI_PREVIEW_SCENES.map((s) => s.fixture), 'di-mission-request-delete-replies.FIXTURE.json'])].sort().map((f) => {
      const bytes = fs.readFileSync(path.join(root, 'test/fixtures', f));
      return { path: `webview-ui/test/fixtures/${f}`, sha256: crypto.createHash('sha256').update(bytes).digest('hex') };
    });
    fs.writeFileSync(
      path.join(CAPTURE_DIR, 'capture-report.json'),
      `${JSON.stringify(
        {
          fixture: true,
          label: 'FIXTURE / VISUAL TEST captures of the isolated Mission preview (webview-ui/test/diMissionPreviewBrowser.test.ts). Synthetic and recorded data only; nothing live.',
          launch: { cwd: 'integrations/pixel-agents/webview-ui', build: PREVIEW_BUILD_COMMAND.join(' '), serve: PREVIEW_SERVE_COMMAND.join(' '), ran_with: 'a free port in place of the documented one, and DI_PREVIEW_OUT_DIR set to the test’s own temporary directory' },
          fixtures,
          // one entry per file: a view captured twice keeps its LAST capture, the one on disk
          captures: [...new Map(report.map((c) => [String(c.file), c])).values()].sort((a, b) => String(a.file).localeCompare(String(b.file))),
        },
        null,
        2,
      )}\n`,
    );
  }
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (Mission preview): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

/** Run one launch command from webview-ui to its end. */
function run(command: readonly string[], env: NodeJS.ProcessEnv): Promise<{ code: number | null; output: string }> {
  return new Promise((resolve) => {
    let output = '';
    const child = spawn(command[0]!, command.slice(1), { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
    child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
    child.stderr?.on('data', (c: Buffer) => (output += c.toString()));
    child.on('exit', (code) => resolve({ code, output }));
  });
}

/** A loopback port free right now (the documented 5179 may be in use; it is the only argument the test changes). */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const addr = probe.address();
      probe.close(() => resolve(typeof addr === 'object' && addr !== null ? addr.port : 0));
    });
  });
}

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const rowSel = (ref: string) => `[data-testid="di-missions-row"][data-mission-ref="${ref}"]`;
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const traffic = async (): Promise<DiPreviewTraffic[]> => (await (await fetch(`${origin}${PREVIEW_TRAFFIC}`)).json()) as DiPreviewTraffic[];

/** Every HTTP request a page ATTEMPTED: each one it issued, and each one that failed with its reason. For an isolation claim the attempt is what counts. */
interface Attempts {
  issued: string[];
  failed: Array<{ url: string; reason: string }>;
}
/** Record a page's attempts. Attach it BEFORE the page navigates (boot's `onPage`), or a route contacted while the page boots goes unseen. */
function watchRequests(pg: Page): Attempts {
  const attempts: Attempts = { issued: [], failed: [] };
  pg.on('request', (r) => attempts.issued.push(r.url()));
  pg.on('requestfailed', (r) => attempts.failed.push({ url: r.url(), reason: r.failure()?.errorText ?? 'unknown' }));
  return attempts;
}
/** Whether a URL is the preview's own origin: parsed and compared EXACTLY (scheme, host and port), never as a prefix. A URL that does not parse is not. */
function ownOrigin(url: string): boolean {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}
/** Every attempted URL, issued or failed, outside the preview's own origin. */
const outside = (a: Attempts): string[] => [...new Set([...a.issued, ...a.failed.map((f) => f.url)])].filter((u) => !ownOrigin(u)).sort();
/** THE isolation assertion: nothing the page attempted left the preview's own origin. */
function assertIsolated(a: Attempts): void {
  assert.deepEqual(outside(a), [], 'every request the page attempted, from its first on, went to the preview’s own origin');
}

/**
 * Open a scene the way a person does: its page, the real app, the scene's office state arrived (its FIXTURE label is drawn). `onPage` runs on the new
 * page BEFORE it navigates (so what it attaches sees the boot); `sink` is where the page's noise goes (the clean-console record, unless a page made to
 * misbehave keeps its own).
 */
async function boot(
  scene: string,
  viewport: Viewport = DESKTOP,
  onSocket?: (url: string) => void,
  { onPage, sink = noise }: { onPage?: (pg: Page) => void | Promise<void>; sink?: string[] } = {},
): Promise<void> {
  page = await browser!.newPage({ viewport, deviceScaleFactor: 1 });
  await onPage?.(page);
  if (onSocket) page.on('websocket', (ws) => onSocket(ws.url()));
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') sink.push(`${scene} ${viewport.width}: console ${m.type()}: ${m.text()}${m.location().url ? ` (at ${m.location().url})` : ''}`);
  });
  page.on('pageerror', (e) => sink.push(`${scene} ${viewport.width}: page error: ${String(e)}`));
  page.on('response', (r) => {
    if (r.status() >= 400) sink.push(`${scene} ${viewport.width}: HTTP ${r.status()} ${r.url()}`);
  });
  page.on('requestfailed', (r) => sink.push(`${scene} ${viewport.width}: request failed: ${r.failure()?.errorText ?? 'unknown'} ${r.url()}`));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}${PREVIEW_SCENE_PAGE}?scene=${scene}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(sel('di-rail-missions'), { timeout: 30_000 });
  await page.waitForFunction(() => document.body.innerText.includes('FIXTURE'), undefined, { timeout: 30_000 });
}

async function missions(filter: string): Promise<void> {
  await p().click(sel('di-rail-missions'));
  await p().click(sel(`di-missions-status-${filter}`));
  await p().waitForSelector(sel('di-missions-page'));
}

async function details(ref: string, filter: string): Promise<void> {
  await missions(filter);
  await p().click(`${rowSel(ref)} ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}

/** A real hold on the trash: pointer down, held past the hold time, pointer up. */
async function hold(): Promise<void> {
  await p().locator(sel('di-scan-trash')).first().scrollIntoViewIfNeeded();
  await p().locator(sel('di-scan-trash')).first().hover();
  await p().mouse.down();
  await p().waitForTimeout(DI_DELETE_HOLD_MS + 400);
  await p().mouse.up();
}

/** A status mark as drawn: its word, its glyph (null where it has none), and whether a plain dot stands in its place. */
interface Mark {
  word: string;
  glyph: string | null;
  dot: boolean;
}
/** Read the marks under `selector` (each a tag, a pill or a rail item): the glyph read apart from the word beside it. */
const marks = (selector: string): Promise<Mark[]> =>
  p().evaluate(
    (s) =>
      [...document.querySelectorAll<HTMLElement>(s)].map((el) => {
        const glyph = el.querySelector('[data-testid="di-status-glyph"]');
        const copy = el.cloneNode(true) as HTMLElement;
        copy.querySelector('[data-testid="di-status-glyph"]')?.remove();
        return { word: (copy.textContent ?? '').trim(), glyph: glyph ? (glyph.textContent ?? '') : null, dot: el.querySelector('.di-scan-dot') !== null };
      }),
    selector,
  );
/** Each listed row's Status-column mark (the fifth cell's tag), by Mission. */
const tableMarks = (): Promise<Record<string, Mark>> =>
  p().evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-missions-row"]')].map((tr) => {
        const tag = tr.querySelectorAll('td')[4]!;
        const glyph = tag.querySelector('[data-testid="di-status-glyph"]');
        const copy = tag.cloneNode(true) as HTMLElement;
        copy.querySelector('[data-testid="di-status-glyph"]')?.remove();
        return [tr.dataset.missionRef!, { word: (copy.textContent ?? '').trim(), glyph: glyph ? (glyph.textContent ?? '') : null, dot: tag.querySelector('.di-scan-dot') !== null }];
      }),
    ),
  );
const tableStatuses = async (): Promise<Record<string, string>> => Object.fromEntries(Object.entries(await tableMarks()).map(([ref, m]) => [ref, m.word]));
/** The canonical mark of a status word: its glyph (diMissionStatus.ts). */
const glyphOf = (word: string) => DI_MISSION_STATUS[LABEL_OF.get(word)!].glyph;

/** What a person can measure on the page now: the page's own overflow, and each named element's box against the viewport and its scroll container. */
const measure = (testids: string[]) =>
  p().evaluate((ids) => {
    const seen = (el: Element) => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.min(r.left + r.width / 2, innerWidth - 1), Math.min(r.top + Math.min(r.height, 12) / 2, innerHeight - 1));
      return { left: Math.round(r.left), right: Math.round(r.right), inViewport: r.left >= 0 && r.right <= innerWidth + 0.5, onTop: hit !== null && el.contains(hit) };
    };
    return {
      pageOverflowsX: document.documentElement.scrollWidth > innerWidth,
      elements: Object.fromEntries(ids.map((id) => [id, [...document.querySelectorAll(`[data-testid="${id}"]`)].map(seen)])),
    };
  }, testids);

/**
 * Write the current view (when capturing) and keep what was measured with it. The fit checks scroll what they measure into view, so by default a
 * capture first RESETS every scroller to its top (a list is shown from its first row, never at a position a measurement left behind); `scroll:
 * 'as-is'` keeps the position for a capture that deliberately shows an answer or a reason scrolled into view.
 */
async function snap(name: string, scene: string, viewport: Viewport, measured: unknown = null, scroll: 'top' | 'as-is' = 'top'): Promise<void> {
  if (CAPTURE_DIR === undefined) return;
  const file = `${name}-${viewport.width}.png`;
  if (scroll === 'top')
    await p().evaluate(() => {
      // EVERY scroller a measurement may have moved (the stage, the details, the lists, the rail and its Orchestrators), not a named few
      for (const el of document.querySelectorAll<HTMLElement>('*')) if (el.scrollTop !== 0 || el.scrollLeft !== 0) el.scrollTo(0, 0);
      window.scrollTo(0, 0);
    });
  await p().waitForTimeout(400);
  const bytes = await p().screenshot({ path: path.join(CAPTURE_DIR, file) });
  const s = DI_PREVIEW_SCENES.find((x) => x.id === scene)!;
  report.push({
    file,
    scene,
    title: s.title,
    viewport,
    fixture: `webview-ui/test/fixtures/${s.fixture}`,
    pick: s.pick,
    provenance: s.provenance,
    synthetic: s.synthetic,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    scroll,
    measured,
  });
}

test('ONE PATH: the docs give exactly the commands this test ran, and the scene index it serves lists every scene and opens nothing', async () => {
  const docs = fs.readFileSync(DOCS, 'utf-8');
  for (const command of [PREVIEW_BUILD_COMMAND, PREVIEW_SERVE_COMMAND]) assert.ok(docs.includes(command.join(' ')), `the docs quote \`${command.join(' ')}\``);
  assert.ok(docs.includes(`http://localhost:${PREVIEW_PORT}${PREVIEW_INDEX}`), 'the docs name the index address');
  page = await browser!.newPage({ viewport: DESKTOP });
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') noise.push(`index: console ${m.type()}: ${m.text()}`);
  });
  page.on('response', (r) => {
    if (r.status() >= 400) noise.push(`index: HTTP ${r.status()} ${r.url()}`);
  });
  page.on('requestfailed', (r) => noise.push(`index: request failed: ${r.failure()?.errorText ?? 'unknown'} ${r.url()}`));
  const sockets: string[] = [];
  page.on('websocket', (ws) => sockets.push(ws.url()));
  await page.goto(`${origin}${PREVIEW_INDEX}`);
  await page.waitForTimeout(1500);
  const links = await page.locator('a[href*="di-mission-preview-scene.html?scene="]').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
  assert.deepEqual(links, DI_PREVIEW_SCENES.map((s) => `${PREVIEW_SCENE_PAGE}?scene=${s.id}`));
  assert.match(await page.locator('h1').innerText(), /^FIXTURE \/ VISUAL TEST/);
  assert.deepEqual(sockets, [], 'the index opens no socket at all');
  await snap('index', DI_PREVIEW_SCENES[0]!.id, DESKTOP);
}, STEPS_MS);

test('ISOLATION: the launch path starts no office server and names no panel, delivery or Herdr route; from its first request on, a page attempts nothing outside the preview’s own origin (and a negative control shows that check catching what it must); a page is answered only from recorded replies or a declared synthetic one', async () => {
  const sources = ['test/harness/di-mission-preview.config.ts', 'test/harness/diMissionPreview.ts'].map((f) => [f, fs.readFileSync(path.join(root, f), 'utf-8')] as const);
  for (const [file, text] of sources) {
    const code = text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
    for (const forbidden of [/di-cli/, /--state-dir/, /--fixture-source/, /--mission-panel-discovery/, /--delivery-adapter-discovery/, /--herdr-/, /child_process/, /\bspawn\(/, /homedir/, /\.pixel-agents/, /discovery/i])
      assert.doesNotMatch(code, forbidden, `${file} must not reach ${forbidden}`);
  }
  // the comparison is by ORIGIN, never by prefix: each of these begins with the preview's origin and is another place, or no address at all
  for (const lookalike of [`${origin}@127.0.0.1/`, `${origin}0/`, `${origin}.example.com/`]) {
    assert.ok(lookalike.startsWith(origin));
    assert.equal(ownOrigin(lookalike), false, `${lookalike} is not the preview’s origin`);
  }
  const sockets: string[] = [];
  let attempts: Attempts = { issued: [], failed: [] };
  // the request observer is attached BEFORE the page navigates, so a route contacted while it boots is seen
  await boot('delete-pending', DESKTOP, (ws) => sockets.push(ws), {
    onPage: (pg) => {
      attempts = watchRequests(pg);
    },
  });
  await details(DELETE_REF, 'archived');
  await hold();
  await p().waitForSelector(`[data-testid="di-scan-delete-result"][data-mission-ref="${DELETE_REF}"]`, { timeout: 10_000 });
  // it saw the boot: the first request it recorded is the scene page's own document, then the build's own assets
  assert.equal(attempts.issued[0], `${origin}${PREVIEW_SCENE_PAGE}?scene=delete-pending`, 'the observer saw the page from its very first request');
  assert.ok(attempts.issued.length > 1, `the observer saw the page load its assets (${attempts.issued.length} requests)`);
  assertIsolated(attempts);
  assert.deepEqual(attempts.failed, [], 'and none of the requests failed');
  assert.deepEqual(sockets, [`${origin.replace('http:', 'ws:')}/ws`], 'the ONE socket the page opens is the preview’s own /ws (a production build: no development client socket)');
  const seen = await traffic();
  assert.ok(seen.length > 0, 'the preview recorded what the page sent');
  for (const t of seen) {
    const scene = DI_PREVIEW_SCENES.find((s) => s.id === t.scene)!;
    if (t.answered === 'scene_state') assert.equal(t.message.type, 'webviewReady');
    else if (t.answered === 'recorded_reply') assert.deepEqual([t.message.type, t.message.op, typeof scene.deleteReply], ['diMissionRequest', 'deleteLocalData', 'string']);
    else if (t.answered === 'declared_synthetic') assert.deepEqual([t.message.type, t.message.op, scene.deliveryStatus], ['diDeliveryRequest', 'status', true]);
    else assert.equal(t.answered, 'never');
  }
  assert.ok(seen.some((t) => t.answered === 'recorded_reply'), 'the delete was answered from the recorded reply');

  // NEGATIVE CONTROL: the same observer, attached the same way, and the same assertion, on a page made to attempt two outside requests WHILE IT BOOTS
  // (an init script, run before the app's own code): one answered, one that fails. Both go to a loopback server this test owns and closes, nowhere else.
  // The assertion must catch both. An observer that never fired would pass the check above and fail here.
  const hits: string[] = [];
  const control = http.createServer((req, res) => {
    hits.push(req.url ?? '');
    if (req.url === '/negative-control/fails') req.socket.destroy();
    else res.writeHead(200, { 'Content-Type': 'text/plain', 'Access-Control-Allow-Origin': '*', Connection: 'close' }).end('answered');
  });
  await new Promise<void>((resolve) => control.listen(0, '127.0.0.1', resolve));
  try {
    const at = `http://127.0.0.1:${(control.address() as net.AddressInfo).port}`;
    const induced = [`${at}/negative-control/answered`, `${at}/negative-control/fails`];
    const controlNoise: string[] = [];
    let caught: Attempts = { issued: [], failed: [] };
    await page!.close();
    await boot('delete-pending', DESKTOP, undefined, {
      sink: controlNoise,
      onPage: async (pg) => {
        caught = watchRequests(pg);
        await pg.addInitScript((urls) => {
          for (const u of urls) void fetch(u).catch(() => undefined);
        }, induced);
      },
    });
    const until = Date.now() + 10_000;
    while (outside(caught).length < induced.length || caught.failed.length === 0) {
      assert.ok(Date.now() < until, `the induced attempts were observed (${JSON.stringify(caught)})`);
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.deepEqual(outside(caught), [...induced].sort(), 'the observer saw both outside attempts the page made while it booted');
    assert.deepEqual([...new Set(caught.failed.map((f) => f.url))], [induced[1]], `and recorded the one that failed as an attempt (${JSON.stringify(caught.failed)})`);
    assert.throws(
      () => assertIsolated(caught),
      (e: unknown) => e instanceof assert.AssertionError && JSON.stringify(e.actual) === JSON.stringify([...induced].sort()),
      'THE isolation assertion fails on exactly them',
    );
    assert.deepEqual([...new Set(hits)].sort(), ['/negative-control/answered', '/negative-control/fails'], 'both attempts really left the page');
    // its own noise is only what it was made to do, never something hidden: each entry names the induced failure
    assert.ok(controlNoise.length > 0, 'the failed attempt was noticed as noise');
    for (const n of controlNoise) assert.ok(n.includes(induced[1]!), `the control page's noise is the induced failure only: ${n}`);
  } finally {
    await page?.close();
    page = null;
    control.closeAllConnections();
    await new Promise((resolve) => control.close(resolve));
  }
}, STEPS_MS);

test('THE VOCABULARY: across the scenes the Missions table shows each of the 15 statuses, every one in the contract’s words with ITS canonical glyph, and nothing else (no “Not read” status)', async () => {
  const shown = new Map<DiMissionStatusId, string[]>();
  for (const scene of DI_PREVIEW_SCENES) {
    if (scene.id.startsWith('delete-') && scene.id !== 'delete-in-flight') continue; // the same office state as delete-in-flight
    await boot(scene.id);
    if (scene.disconnectAfterMs !== undefined) await p().waitForTimeout(scene.disconnectAfterMs + 1500);
    const words: string[] = [];
    let captured = false;
    for (const filter of ['all', 'archived']) {
      await missions(filter);
      const listed = await tableMarks();
      for (const [ref, mark] of Object.entries(listed)) {
        words.push(mark.word);
        // the status's OWN glyph, never a generic dot: the same mark for the same status on every surface
        if (LABEL_OF.has(mark.word)) assert.deepEqual([mark.glyph, mark.dot], [glyphOf(mark.word), false], `${scene.id} ${ref}: ${mark.word} wears its glyph`);
      }
      // the scene's Missions under All, or under Archived where All lists none (a scene whose Missions are all filed closed)
      if (!captured && Object.keys(listed).length > 0) {
        await snap(`missions-${scene.id}`, scene.id, DESKTOP, { filter });
        captured = true;
      }
    }
    const ids = [...new Set(words)].map((w) => {
      const id = LABEL_OF.get(w);
      assert.ok(id !== undefined, `${scene.id}: “${w}” is a word of the closed vocabulary`);
      return id!;
    });
    assert.deepEqual(ids.sort(), [...scene.statuses].sort(), `${scene.id} reads exactly its declared statuses`);
    for (const id of ids) shown.set(id, [...(shown.get(id) ?? []), scene.id]);
    await page!.close();
    page = null;
  }
  assert.deepEqual([...shown.keys()].sort(), [...DI_MISSION_STATUS_IDS].sort(), 'all 15, exactly');
  assert.equal(DI_MISSION_STATUS_IDS.length, 15);
  assert.ok(!(DI_MISSION_STATUS_IDS as readonly string[]).includes('not_read'), 'not_read is a fact condition, never a status');
  assert.equal(new Set(DI_MISSION_STATUS_IDS.map((id) => DI_MISSION_STATUS[id].glyph)).size, 15, 'each status has its own glyph');
}, 600_000);

test('ONE MISSION, ONE STATUS: at its task-observed step the lifecycle Mission reads Task observed, with the SAME canonical glyph, on the table, its details, Reception and the rail', async () => {
  const { label: word, glyph } = DI_MISSION_STATUS.task_observed;
  const same = { word, glyph, dot: false };
  await boot('lifecycle-task-observed');
  // the row's second line is what the SOURCE RECORD says (its own lifecycle literal and source), attributed as such, beside the contract's status
  const homeLine = await p().locator('.di-scan-row .di-scan-context').first().textContent();
  assert.match(homeLine ?? '', /^Record: \S/, `Home's row says its record's state as the record's (${homeLine})`);
  await missions('all');
  assert.match((await p().locator(`${rowSel(LIFECYCLE_REF)} .di-missions-step`).textContent()) ?? '', /^Record: RUNNING · Mc$/, 'the Missions row attributes its second line to the record');
  assert.deepEqual((await tableMarks())[LIFECYCLE_REF], same, 'the Missions table');
  const title = (await p().locator(`${rowSel(LIFECYCLE_REF)} .di-missions-title`).textContent())!.slice(0, 20);
  const rail = await p().evaluate((t) => {
    const item = [...document.querySelectorAll<HTMLElement>('[data-testid="di-rail-orchestrator"]')].find((x) => (x.textContent ?? '').includes(t))!;
    return { word: (item.querySelector('[data-testid="di-rail-orchestrator-status"]')?.textContent ?? '').trim(), glyph: item.querySelector('[data-testid="di-status-glyph"]')?.textContent ?? null, dot: item.querySelector(':scope > .di-scan-dot') !== null };
  }, title);
  assert.deepEqual(rail, same, 'the rail');
  await p().click(`${rowSel(LIFECYCLE_REF)} ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  const pills = await marks(sel('di-scan-pill'));
  assert.ok(pills.some((m) => m.word === word && m.glyph === glyph && !m.dot), `its details (${JSON.stringify(pills)})`);
  // attention is another vocabulary: its pill keeps the dot and never wears a status glyph
  for (const m of pills.filter((x) => x.word === 'Needs you')) assert.deepEqual([m.glyph, m.dot], [null, true], 'Needs you keeps its dot');
  assert.ok(!pills.some((m) => /Running|Building|Team working/.test(m.word)), 'never a working word');
  await snap('details-lifecycle-task-observed', 'lifecycle-task-observed', DESKTOP);
  await p().click(sel('di-open-reception'));
  await p().waitForSelector(sel('di-reception-page'));
  const reception = await p().evaluate(
    (ref) =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-reception-card"]')]
        .filter((c) => c.dataset.missionRef === ref || c.querySelector(`[data-mission-ref="${ref}"]`) !== null)
        .map((c) => {
          const tag = c.querySelector<HTMLElement>('[data-testid="di-reception-mission-status"]')!;
          const copy = tag.cloneNode(true) as HTMLElement;
          copy.querySelector('[data-testid="di-status-glyph"]')?.remove();
          return { word: (copy.textContent ?? '').trim(), glyph: tag.querySelector('[data-testid="di-status-glyph"]')?.textContent ?? null, dot: tag.querySelector('.di-scan-dot') !== null };
        }),
    LIFECYCLE_REF,
  );
  assert.ok(reception.length > 0, 'the Mission has a Reception card');
  for (const m of reception) assert.deepEqual(m, same, 'Reception');
  await snap('reception-lifecycle-task-observed', 'lifecycle-task-observed', DESKTOP);
}, STEPS_MS);

test('NEVER STARTED, START REQUESTED, TASK OBSERVED, WORKING: the lifecycle Mission’s details say each step plainly, and only the roster’s live agent reads Running', async () => {
  for (const [scene, status] of [
    ['lifecycle-approved', 'not_started'],
    ['lifecycle-start-requested', 'unknown'],
    ['lifecycle-task-observed', 'task_observed'],
  ] as const) {
    await boot(scene);
    await details(LIFECYCLE_REF, 'all');
    const pills = await marks(sel('di-scan-pill'));
    assert.ok(pills.some((m) => m.word === DI_MISSION_STATUS[status].label && m.glyph === DI_MISSION_STATUS[status].glyph), `${scene}: ${JSON.stringify(pills)}`);
    assert.ok(!pills.some((m) => m.word === DI_MISSION_STATUS.running.label), `${scene} never reads Running`);
    await snap(`details-${scene}`, scene, DESKTOP);
    await page!.close();
    page = null;
  }
  await boot('herdr-roster');
  await missions('all');
  const roster = await tableStatuses();
  const working = Object.entries(roster).filter(([, w]) => w === DI_MISSION_STATUS.running.label).map(([ref]) => ref);
  assert.deepEqual(working.map((ref) => ref.split('@')[0]), ['hd:task-working'], 'Running is the task whose live agent is at work, and no other');
  assert.equal(Object.entries(roster).find(([ref]) => ref.startsWith('hd:task-unverified@'))?.[1], DI_MISSION_STATUS.unavailable.label, 'no live session corroborates it: Unavailable, never Not started');
  await p().click(sel('di-office-panel-minimize'));
  await snap('office-herdr-roster', 'herdr-roster', DESKTOP);
}, STEPS_MS);

test('FILED AT ONCE, READ-ONLY: the finished Mission leaves every filter but Archived; its actions are disabled with their reasons drawn, and only the owner’s hold is offered', async () => {
  await boot('lifecycle-archived');
  for (const filter of NON_ARCHIVED_FILTERS) {
    await missions(filter);
    assert.equal(await p().locator(rowSel(LIFECYCLE_REF)).count(), 0, `not under ${filter}`);
    if (filter === 'all') await snap('missions-all-after-finish', 'lifecycle-archived', DESKTOP);
  }
  await missions('archived');
  assert.equal(await p().locator(rowSel(LIFECYCLE_REF)).count(), 1, 'under Archived');
  assert.equal(await p().locator(rowSel(OVERDUE_REF)).count(), 1, 'beside the Mission finished before it');
  await snap('missions-archived', 'lifecycle-archived', DESKTOP);
  await p().click(`${rowSel(LIFECYCLE_REF)} ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  assert.equal(((await p().locator(sel('di-scan-archived-note')).textContent()) ?? '').trim(), ARCHIVED_READ_ONLY);
  assert.equal(await p().locator(sel('di-scan-herdr')).isDisabled(), true, 'Herdr is disabled');
  // the archived reason EXACTLY, and beside it only the one child the product offers there: "Check again", a re-read of the Herdr status
  // (diHerdrModel.ts canRecheck; a status read, never an action: archived is read-only, not information-erasing)
  const reason = await p().locator(sel('di-scan-herdr-reason')).evaluate((el) => {
    const copy = el.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('[data-testid="di-scan-herdr-recheck"]').forEach((x) => x.remove());
    return {
      own: (copy.textContent ?? '').trim(),
      children: [...el.children].map((c) => [c.tagName, c.getAttribute('data-testid'), (c.textContent ?? '').trim()]),
    };
  });
  assert.equal(reason.own, ARCHIVED_READ_ONLY, 'with the archived reason drawn beside it');
  for (const child of reason.children) assert.deepEqual(child, ['BUTTON', 'di-scan-herdr-recheck', 'Check again'], 'its only child is the status re-check');
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 1, 'the owner’s hold is offered');
  const reasons = await measure(['di-scan-archived-note', 'di-scan-herdr-reason', 'di-scan-trash-reason']);
  await p().locator(sel('di-scan-trash-reason')).scrollIntoViewIfNeeded();
  await snap('details-lifecycle-archived', 'lifecycle-archived', DESKTOP, reasons, 'as-is');
}, STEPS_MS);

test('RECLAIMED AND DELETED: the archived Mission’s details say what happened to its local data, and a deleted Mission’s hold is not offered again', async () => {
  await boot('lifecycle-reclaimed');
  await details(LIFECYCLE_REF, 'archived');
  await p().locator(sel('di-scan-trash-reason')).scrollIntoViewIfNeeded();
  await snap('details-lifecycle-reclaimed', 'lifecycle-reclaimed', DESKTOP, null, 'as-is');
  await page!.close();
  page = null;
  await boot('lifecycle-deleted');
  await details(LIFECYCLE_REF, 'archived');
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0, 'no hold on a deleted Mission');
  assert.match((await p().locator(sel('di-scan-trash-reason')).textContent()) ?? '', /already deleted/);
  await p().locator(sel('di-scan-trash-reason')).scrollIntoViewIfNeeded();
  await snap('details-lifecycle-deleted', 'lifecycle-deleted', DESKTOP, null, 'as-is');
}, STEPS_MS);

test('OWNER HOLD-TO-DELETE: a running Mission refuses it with its reason; on the archived Mission each recorded answer is shown where it was asked', async () => {
  await boot('delete-in-flight');
  await details(RUNNING_REF, 'all');
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0);
  assert.equal(((await p().locator(sel('di-scan-trash-reason')).textContent()) ?? '').trim(), 'A running Mission cannot be deleted.');
  await p().locator(sel('di-scan-trash-reason')).scrollIntoViewIfNeeded();
  await snap('delete-running-refused', 'delete-in-flight', DESKTOP, null, 'as-is');
  await details(DELETE_REF, 'archived');
  await hold();
  await p().waitForSelector(`[data-testid="di-scan-delete-result"][data-mission-ref="${DELETE_REF}"][data-state="in_flight"]`);
  assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0, 'withdrawn while it waits');
  await snap('delete-in-flight', 'delete-in-flight', DESKTOP, null, 'as-is');
  await page!.close();
  page = null;
  for (const [scene, state, words] of [
    ['delete-pending', 'pending', new RegExp(`^${escape(MR_DELETE_PENDING_WORDS)}`)],
    ['delete-refused-busy', 'refused', /^Not deleted: a progression pass is acting on it right now; try again shortly\. Nothing was removed\./],
    ['delete-refused-stale', 'refused', /^Not deleted: the Mission changed since this page showed it\. Nothing was removed\./],
    ['delete-completed', 'completed', new RegExp(`^${escape(MR_DELETE_RECLAIMED_WORDS)}`)],
  ] as const) {
    await boot(scene);
    await details(DELETE_REF, 'archived');
    await hold();
    const result = p().locator(`[data-testid="di-scan-delete-result"][data-mission-ref="${DELETE_REF}"]`);
    await result.waitFor({ timeout: 10_000 });
    assert.equal(await result.getAttribute('data-state'), state, scene);
    assert.match(((await result.textContent()) ?? '').trim(), words, scene);
    await result.scrollIntoViewIfNeeded();
    await snap(scene, scene, DESKTOP, null, 'as-is');
    await page!.close();
    page = null;
  }
}, STEPS_MS);

test('ARCHIVED DELIVERY: read-only, still readable: the linked task’s status is read and its facts shown, with the confirmation and the step disabled beside the archived reason', async () => {
  await boot('archived-delivery');
  await details(LINKED_DELIVERY, 'archived');
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-scan-delivery-read"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-scan-delivery-read'));
  await p().waitForSelector(sel('di-dl-view'));
  assert.equal(await p().locator(sel('di-dl-confirm')).isDisabled(), true);
  assert.equal(await p().locator(sel('di-dl-advance')).isDisabled(), true);
  assert.equal(((await p().locator(sel('di-scan-delivery-archived-act')).textContent()) ?? '').trim(), DI_DELIVERY_ARCHIVED_ACT);
  await p().locator(sel('di-dl-view')).scrollIntoViewIfNeeded();
  await snap('archived-delivery-read', 'archived-delivery', DESKTOP, null, 'as-is');
  const sent = (await traffic()).filter((t) => t.scene === 'archived-delivery' && t.message.type === 'diDeliveryRequest').map((t) => t.message.op);
  assert.ok(!sent.includes('advanceOnce'), 'no step was ever sent');
}, STEPS_MS);

test('KEYBOARD: from the rail to an archived Mission’s details and its hold, by Tab and Enter alone, each stop visibly focused', async () => {
  await boot('lifecycle-archived');
  const focus = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? '');
  const focusedRow = () => p().evaluate(() => document.activeElement?.closest('[data-testid="di-missions-row"]')?.getAttribute('data-mission-ref') ?? null);
  const tabTo = async (testid: string, until: () => Promise<boolean> = async () => true, max = 80) => {
    for (let i = 0; i < max; i++) {
      await p().keyboard.press('Tab');
      if ((await focus()) === testid && (await until())) return;
    }
    throw new Error(`${testid} was not reached by Tab`);
  };
  const outlined = () =>
    p().evaluate(() => {
      const s = getComputedStyle(document.activeElement!);
      return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    });
  await tabTo('di-rail-missions');
  assert.ok(await outlined(), 'the rail item shows its focus');
  await p().keyboard.press('Enter');
  await tabTo('di-missions-status-archived');
  assert.ok(await outlined(), 'the Archived filter shows its focus');
  await p().keyboard.press('Enter');
  await tabTo('di-missions-open', async () => (await focusedRow()) === LIFECYCLE_REF);
  assert.ok(await outlined(), 'the row shows its focus');
  await snap('keyboard-archived-row', 'lifecycle-archived', DESKTOP, null, 'as-is');
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await tabTo('di-scan-trash');
  assert.ok(await outlined(), 'the hold shows its focus');
  assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-hold')), 'true', 'the focused trash is the owner’s hold');
  await snap('keyboard-archived-hold', 'lifecycle-archived', DESKTOP, null, 'as-is');
}, STEPS_MS);

/**
 * Whether each element under `selector` is drawn WHOLE, in BOTH dimensions, once brought into view: nothing of its content past its own box (scroll
 * width AND height), every line of its text inside its border box (a wrapped word above or below its border fails), and its box inside the region
 * that can show it: the window cut by every ancestor that clips or scrolls (left, right, top AND bottom). A box taller than that region (a long
 * paragraph in a short window) cannot be inside it at once: it is checked at both ends instead, its top brought into view inside it, then its bottom.
 */
const whole = (selector: string) =>
  p().evaluate((s) => {
    return [...document.querySelectorAll<HTMLElement>(s)].map((el) => {
      // what can show it: the window, cut by every ancestor that clips or scrolls it (its stage, the details' scroller, the rail, the panel). Along
      // the CONTAINING-BLOCK chain only: a position: fixed box (the scan panes are) escapes the overflow of every ancestor above it, unless that
      // ancestor makes itself the containing block of fixed boxes (a transform, a filter, a perspective, or paint/layout containment)
      const holderOf = () => {
        let [l, t, rt, b] = [0, 0, innerWidth, innerHeight];
        let escaped = getComputedStyle(el).position === 'fixed';
        for (let a = el.parentElement; a !== null; a = a.parentElement) {
          const st = getComputedStyle(a);
          const holdsFixed = st.transform !== 'none' || st.filter !== 'none' || st.perspective !== 'none' || /paint|layout|strict|content/.test(st.contain);
          if (escaped && !holdsFixed) continue;
          escaped = st.position === 'fixed';
          if (st.overflowX === 'visible' && st.overflowY === 'visible') continue;
          const box = a.getBoundingClientRect();
          [l, t, rt, b] = [Math.max(l, box.left), Math.max(t, box.top), Math.min(rt, box.right), Math.min(b, box.bottom)];
        }
        return new DOMRect(l, t, rt - l, b - t);
      };
      const top = (h: DOMRect) => Math.max(0, h.top);
      const bottom = (h: DOMRect) => Math.min(innerHeight, h.bottom);
      el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      let r = el.getBoundingClientRect();
      let holder = holderOf();
      const sideways = r.left >= Math.max(0, holder.left) - 0.5 && r.right <= Math.min(innerWidth, holder.right) + 0.5;
      const tall = r.height > bottom(holder) - top(holder);
      let upright: boolean;
      if (!tall) upright = r.top >= top(holder) - 0.5 && r.bottom <= bottom(holder) + 0.5;
      else {
        el.scrollIntoView({ block: 'start' });
        r = el.getBoundingClientRect();
        holder = holderOf();
        const topIn = r.top >= top(holder) - 0.5 && r.top < bottom(holder);
        el.scrollIntoView({ block: 'end' });
        r = el.getBoundingClientRect();
        holder = holderOf();
        upright = topIn && r.bottom <= bottom(holder) + 0.5 && r.bottom > top(holder);
      }
      r = el.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(el);
      const lines = [...range.getClientRects()].filter((x) => x.width > 0 && x.height > 0);
      const content = el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1;
      const text = lines.every((x) => x.left >= r.left - 1 && x.right <= r.right + 1 && x.top >= r.top - 1 && x.bottom <= r.bottom + 1);
      return { text: (el.textContent ?? '').trim().slice(0, 80), whole: content && text && sideways && upright, content, lines: text, sideways, upright, tall };
    });
  }, selector);
const allWhole = async (selector: string, what: string) => {
  const found = await whole(selector);
  assert.ok(found.length > 0, `${what}: something to measure`);
  for (const x of found) assert.ok(x.whole, `${what} is drawn whole: ${JSON.stringify(x)}`);
};

/** Overlap, in px², of two boxes. */
const overlap = (a: DOMRect | { left: number; right: number; top: number; bottom: number }, b: typeof a) =>
  Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));

for (const viewport of [DESKTOP, ...NARROWS]) {
  test(`THE FIXTURE LABEL at ${viewport.width} x ${viewport.height}: drawn whole, in its own space, over nothing (never on the rail’s wordmark)`, async () => {
    await boot('lifecycle-task-observed', viewport);
    await missions('all');
    const boxes = await p().evaluate(() => {
      const box = (s: string) => {
        const el = document.querySelector<HTMLElement>(s);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, clipped: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1 };
      };
      return {
        strip: box('[data-testid="di-fixture-banner-in-panel"]'),
        floating: box('[data-testid="di-fixture-banner"]'),
        wordmark: box('.di-rail-wordmark'),
        rail: box('[data-testid="di-rail"]'),
        stage: box('[data-testid="di-scan-stage"]'),
      };
    });
    const label = boxes.strip ?? boxes.floating;
    assert.ok(label !== null, 'a fixture label is drawn');
    assert.equal(label!.clipped, false, 'its words are whole');
    assert.equal(overlap(label!, boxes.wordmark!), 0, 'it is not on the rail’s wordmark');
    if (boxes.strip !== null) {
      assert.equal(overlap(boxes.strip, boxes.rail!), 0, 'the rail starts below the strip');
      assert.equal(overlap(boxes.strip, boxes.stage!), 0, 'the stage starts below the strip');
    }
  }, STEPS_MS);
}

for (const viewport of NARROWS) {
  test(`NARROW (${viewport.width} x ${viewport.height}): every chip, status, row and reason is drawn whole in both dimensions inside its stage, panel or rail, with no sideways page scroll`, async () => {
    await boot('herdr-roster', viewport);
    // Home: the panel's own list, past the rail, each row's title given room and every pill whole
    const clear = await p().evaluate(() => {
      const rail = document.querySelector('[data-testid="di-rail"]')!.getBoundingClientRect();
      return [...document.querySelectorAll<HTMLElement>('.di-scan-row')].map((row) => ({
        clearOfRail: row.getBoundingClientRect().left >= rail.right - 0.5,
        titleWidth: Math.round(row.querySelector<HTMLElement>('.di-scan-title')!.getBoundingClientRect().width),
      }));
    });
    assert.ok(clear.length > 0);
    for (const row of clear) assert.deepEqual([row.clearOfRail, row.titleWidth > 40], [true, true], `a Home row sits beside the rail with room for its title (${JSON.stringify(row)})`);
    await allWhole('.di-scan-row .di-scan-pill', 'a Home row’s pill');
    await allWhole('[data-testid="di-rail-orchestrator-status"]', 'a rail status');
    await snap('home-herdr-roster', 'herdr-roster', viewport);
    await missions('all');
    assert.equal(await p().evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'the page never scrolls sideways');
    for (const id of ['di-missions-scope-mine', 'di-missions-scope-team', 'di-missions-status-all', 'di-missions-status-needs', 'di-missions-status-stalled', 'di-missions-status-running', 'di-missions-status-done', 'di-missions-status-archived'])
      await allWhole(sel(id), id);
    await allWhole('[data-testid="di-missions-row"] td:nth-child(5) .di-page-tag', 'a row’s status');
    await allWhole('[data-testid="di-missions-attention"]', 'a row’s Needs you');
    await snap('missions-herdr-roster', 'herdr-roster', viewport);
    await page!.close();
    page = null;

    // every status's pill in its Mission's details, the two-word statuses (Needs you, Task observed, Start requested, Not started) among them
    const fitted = new Set<string>();
    for (const scene of ['herdr-roster', 'workflow-phases', 'lifecycle-approved', 'lifecycle-start-requested', 'lifecycle-task-observed']) {
      await boot(scene, viewport);
      for (const filter of ['all', 'archived']) {
        await missions(filter);
        const refs = await p().locator(sel('di-missions-row')).evaluateAll((rows) => rows.map((r) => r.getAttribute('data-mission-ref')!));
        for (const ref of refs) {
          await missions(filter);
          await p().click(`${rowSel(ref)} ${sel('di-missions-open')}`);
          await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
          for (const x of await whole(sel('di-scan-pill'))) {
            assert.ok(x.whole, `${scene} ${ref}: its pill “${x.text}” is drawn whole: ${JSON.stringify(x)}`);
            fitted.add(x.text);
          }
        }
      }
      if (scene === 'lifecycle-task-observed') {
        // the capture is the LIFECYCLE Mission's details, opened deliberately after the fit loop (which ends on whichever row it measured last)
        await details(LIFECYCLE_REF, 'all');
        await snap('details-lifecycle-task-observed', scene, viewport);
      }
      await page!.close();
      page = null;
    }
    for (const word of ['Needs you', 'Task observed', 'Start requested', 'Not started'])
      assert.ok([...fitted].some((t) => t.endsWith(word)), `the two-word “${word}” was measured (${[...fitted].join(', ')})`);

    await boot('lifecycle-archived', viewport);
    await missions('archived');
    await allWhole('[data-testid="di-missions-row"] td:nth-child(5) .di-page-tag', 'an archived row’s status');
    await snap('missions-archived', 'lifecycle-archived', viewport);
    await p().click(`${rowSel(LIFECYCLE_REF)} ${sel('di-missions-open')}`);
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await allWhole(sel('di-scan-pill'), 'its status pill');
    await snap('details-lifecycle-archived', 'lifecycle-archived', viewport);
    for (const id of ['di-scan-archived-note', 'di-scan-herdr-reason', 'di-scan-trash-reason']) await allWhole(sel(id), id);
    await p().locator(sel('di-scan-trash-reason')).scrollIntoViewIfNeeded();
    await snap('details-lifecycle-archived-reasons', 'lifecycle-archived', viewport, null, 'as-is');
    await page!.close();
    page = null;

    await boot('lifecycle-task-observed', viewport);
    await p().click(sel('di-open-reception'));
    await p().waitForSelector(sel('di-reception-page'));
    await allWhole(sel('di-reception-mission-status'), 'Reception’s Mission status');
    await allWhole(sel('di-reception-count'), 'Reception’s count');
    await p().locator(sel('di-reception-page')).evaluate((el) => el.closest('.di-scan-stage')?.scrollTo(0, 0));
    await snap('reception-lifecycle-task-observed', 'lifecycle-task-observed', viewport);
    await page!.close();
    page = null;

    await boot('delete-refused-busy', viewport);
    await details(DELETE_REF, 'archived');
    await hold();
    await p().locator(`[data-testid="di-scan-delete-result"][data-mission-ref="${DELETE_REF}"]`).waitFor({ timeout: 10_000 });
    await allWhole(sel('di-scan-delete-result'), 'the refusal and its reason');
    await snap('delete-refused-busy', 'delete-refused-busy', viewport, null, 'as-is');
  }, 600_000);
}

test('A CLEAN CONSOLE: no page this test opened logged an error or a warning, or failed a request', () => {
  assert.deepEqual(noise, []);
});
