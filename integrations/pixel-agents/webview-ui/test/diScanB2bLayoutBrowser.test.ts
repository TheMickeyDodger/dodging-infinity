/**
 * Direction B, stage B2b, RENDERED in Chrome (not Brave evidence): two panes at 1440 and wider, DESIGN.md's type ramp in the stage, and the reading
 * cap. Two sources: the real App (test/harness/di-app-scan.html; the office is the session-mismatch v2 FIXTURE in its labelled available-capacity
 * variation; the request panel SYNTHETIC) for the geometry and the key sequence, and the labelled SAMPLE specimen (test/harness/di-scan-lock.html)
 * for populated content in every stage view (Mission details with a long description, Code Diff with files and code, Comments, the rail pages).
 *
 * Each claim names the assertion that carries it (method rule 2):
 *  - TWO PANES (Q9): at 1440 and 1600 a docked screen's right edge is the list dock's left edge and Home is NOT covered; at 1280 the screen covers
 *    the list and Home is covered. Carried by the measured edges and `data-covered`, for Mission details, Code Diff and a rail page;
 *  - ONE GRAMMAR: at 1280 and at 1440 the same keys give the same states: one click opens, the first Escape closes, nothing is marked, focus is on
 *    the dismissed row. Carried by the marks (compared by ref) and the focused element's ref;
 *  - THE RAMP (Q1): every text node inside the stage, in every view, at every width, is drawn at a step of the scale (11, 12, 13, 14, 18 px), and
 *    every 11 px text is an uppercase label (tracked 0.08em when longer than one character). Carried by the list of off-scale texts being EMPTY;
 *  - THE CAP ((a)): Mission details' prose is at most 70ch wide where its card is wider than that. Carried by the measured widths.
 * NOT asserted, measured only (an OPEN INTRODUCED LIMITATION until B2c, b2b-record.md): whether the action row fits or every action is reachable.
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
import { DI_PANEL_WIDTH_PX, DI_RAIL_WIDTH_PX } from '../src/constants.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import { relayReply, SYNTHETIC_CONVERSATION_ID, syntheticConversationSummary, syntheticOverview } from './diNewMissionSynthetic.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
/** Where to write screenshots and the measured JSON (unset: nothing is written). */
const SHOTS = process.env.DI_B2B_SHOTS ?? null;
const SIZES = [
  [1280, 800],
  [1440, 900],
  [1600, 1000],
] as const;
const SCALE = [11, 12, 13, 14, 18];
/**
 * Method rule 4 (the Lead, 2026-11-05): a measurement artifact is NEVER overwritten. A path that already exists gets a fresh numbered name instead, so a
 * second run into the same directory (a narrower -t rerun, say) adds evidence and never replaces the first run's.
 */
function freshPath(dir: string, name: string): string {
  const ext = path.extname(name);
  const stem = name.slice(0, name.length - ext.length);
  let candidate = path.join(dir, name);
  for (let n = 2; fs.existsSync(candidate); n++) candidate = path.join(dir, `${stem}.${n}${ext}`);
  return candidate;
}

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
const measured: Record<string, unknown> = {};
/** What the synthetic request panel's refresh answers (a test may give Recent an ask). */
let overview: Record<string, unknown> = syntheticOverview() as Record<string, unknown>;

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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', overview)));
      }),
    );
  });
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
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
  if (SHOTS !== null) fs.writeFileSync(freshPath(SHOTS, 'b2b-measured.json'), JSON.stringify(measured, null, 2), { flag: 'wx' });
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B2b layout): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  overview = syntheticOverview() as Record<string, unknown>;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
/**
 * The state every capture is named for, ASSERTED BEFORE the capture (the Lead, 2026-11-05; the rule that fixed the Brave v1 manifest): in two panes the
 * list's panel is the dock (its left edge at the window's width minus the dock), Home is NOT covered, and its tabs and a row title are inside that
 * pane AND are what the window shows at their centre (not under the docked screen); in one pane Home is covered. A capture is never taken of a state
 * that was not asserted, so a harness that does not dock fails here instead of producing a picture that looks like a product defect.
 */
async function assertDockState(w: number, what: string): Promise<void> {
  const st = await p().evaluate(() => {
    const dock = document.querySelector('[data-testid="di-office-panel"]')!.getBoundingClientRect();
    const home = document.querySelector('[data-testid="di-scan-home"]');
    const shown = (el: Element | null) => {
      if (el === null) return { inside: false, onTop: false };
      const r = el.getBoundingClientRect();
      const x = (r.left + r.right) / 2;
      const y = (r.top + r.bottom) / 2;
      const hit = document.elementFromPoint(x, y);
      return { inside: r.left >= dock.left - 0.5 && r.right <= dock.right + 0.5 && r.width > 0, onTop: hit !== null && (el === hit || el.contains(hit)) };
    };
    return {
      dockLeft: Math.round(dock.left),
      covered: home?.getAttribute('data-covered') ?? null,
      tabs: ['open', 'merged', 'closed', 'archived'].map((t) => shown(document.querySelector(`[data-testid="di-scan-tab-${t}"]`))),
      title: shown(document.querySelector('[data-testid="di-scan-row"] .di-scan-title')),
    };
  });
  if (w >= 1440) {
    assert.equal(st.dockLeft, w - DI_PANEL_WIDTH_PX, `${what}: the list's panel is the dock`);
    assert.equal(st.covered, null, `${what}: Home is not covered`);
    st.tabs.forEach((t, i) => assert.deepEqual(t, { inside: true, onTop: true }, `${what}: tab ${i} is inside the list's pane and shown`));
    assert.deepEqual(st.title, { inside: true, onTop: true }, `${what}: a row title is inside the list's pane and shown`);
  } else assert.equal(st.covered, 'true', `${what}: one pane, Home is covered`);
}
const shot = async (name: string, w: number) => {
  await assertDockState(w, name);
  if (SHOTS !== null) await p().screenshot({ path: freshPath(SHOTS, `${name}.png`) });
};

/** The real App on the office fixture, on Home. */
async function bootApp(width: number, height: number): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}

/** The labelled SAMPLE specimen, opened on `view` (`query` adds the cast or case). */
async function bootLock(width: number, height: number, view: string, query = ''): Promise<void> {
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=${view}${query}`, { waitUntil: 'load' });
  await page.waitForSelector(sel('di-scan-stage'), { timeout: 20_000 });
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

const edges = () =>
  p().evaluate(() => {
    const stage = document.querySelector('[data-testid="di-scan-stage"]')!.getBoundingClientRect();
    const dock = document.querySelector('[data-testid="di-office-panel"]')!.getBoundingClientRect();
    const home = document.querySelector('[data-testid="di-scan-home"]');
    return {
      stageLeft: Math.round(stage.left),
      stageRight: Math.round(stage.right),
      dockLeft: Math.round(dock.left),
      panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
      covered: home?.getAttribute('data-covered') ?? null,
    };
  });
const marks = () =>
  p().evaluate(() => ({
    rows: [...document.querySelectorAll('[data-testid="di-scan-row"][aria-current]')].map((r) => r.getAttribute('data-mission-ref')),
    stage: document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-mission-ref') ?? null,
  }));

/** The geometry claim for the screen shown now, at window width `w`. */
async function expectDocked(w: number, what: string): Promise<void> {
  const e = await edges();
  measured[`${what} @${w}`] = e;
  assert.equal(e.stageLeft, DI_RAIL_WIDTH_PX, `${what} @${w}: the screen starts at the rail's edge`);
  if (w >= 1440) {
    assert.equal(e.panes, 'two', `${what} @${w}: two panes`);
    assert.equal(e.dockLeft, w - DI_PANEL_WIDTH_PX, `${what} @${w}: the list dock is where it was`);
    assert.equal(e.stageRight, e.dockLeft, `${what} @${w}: the screen ENDS at the list dock (it is docked beside the list)`);
    assert.equal(e.covered, null, `${what} @${w}: Home is NOT covered`);
  } else {
    assert.equal(e.panes, 'one', `${what} @${w}: one pane`);
    assert.equal(e.stageRight, w, `${what} @${w}: the screen covers the list (it runs to the window's edge)`);
    assert.equal(e.covered, 'true', `${what} @${w}: Home is covered`);
  }
}

for (const [w, h] of SIZES)
  test(
    `B2b TWO PANES (Q9) at ${w}x${h}: Mission details, Code Diff and a rail page ${w >= 1440 ? 'dock BESIDE the list, which stays shown' : 'cover the list (one pane)'}`,
    async () => {
      await bootApp(w, h);
      const ref = (await p().locator(sel('di-scan-row')).first().getAttribute('data-mission-ref'))!;
      await p().locator(sel('di-scan-row')).first().click();
      await p().waitForSelector(sel('di-scan-mission-title'));
      await expectDocked(w, 'Mission details');
      await shot(`b2b-${w}x${h}-mission`, w);
      if (w >= 1440) {
        // the list beside it is really there: its rows are drawn, and the docked row is the one marked
        assert.equal(await p().locator(`[data-testid="di-scan-row"][data-mission-ref="${ref}"]`).isVisible(), true, 'the docked Mission\'s row is shown beside it');
        assert.deepEqual(await marks(), { rows: [ref], stage: ref }, 'the marked row and the stage name the same Mission');
      }
      // the action row, MEASURED ONLY (an open introduced limitation until B2c; never asserted here)
      measured[`action row @${w}`] = await p().evaluate(() => {
        const stage = document.querySelector('[data-testid="di-scan-stage"]')!.getBoundingClientRect();
        const items = [...document.querySelectorAll('.di-scan-actions > *')].map((el) => el.getBoundingClientRect().right);
        return { stageRight: Math.round(stage.right), rowRight: Math.round(Math.max(...items)), overflowPx: Math.round(Math.max(...items) - stage.right) };
      });
      // (F-a) the one-line Herdr status line, MEASURED ONLY: its lines and width, the notes column it sits in, the Trash notes beside it
      measured[`herdr line @${w}`] = await p().evaluate(() => {
        const box = (el: Element | null) => (el === null ? null : Math.round(el.getBoundingClientRect().width));
        const line = document.querySelector('[data-testid="di-scan-herdr-reason"]');
        const lh = line === null ? 0 : parseFloat(getComputedStyle(line).lineHeight) || parseFloat(getComputedStyle(line).fontSize) * 1.4;
        return {
          stage: box(document.querySelector('[data-testid="di-scan-stage"]')),
          notesRow: box(document.querySelector('.di-scan-actions-notes')),
          herdrNotes: box(document.querySelector('.di-scan-herdr-notes')),
          trashNotes: box(document.querySelector('.di-scan-trash-notes')),
          herdrLine: box(line),
          herdrLines: line === null ? null : Math.round(line.getBoundingClientRect().height / lh),
          text: line?.textContent?.trim().slice(0, 90) ?? null,
        };
      });
      await p().click(sel('di-scan-code-diff'));
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
      await expectDocked(w, 'Code Diff');
      await shot(`b2b-${w}x${h}-diff`, w);
      await p().keyboard.press('Escape');
      await p().keyboard.press('Escape');
      await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
      await p().click(sel('di-open-reception'));
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="reception"]');
      await expectDocked(w, 'Reception (a rail page)');
      await shot(`b2b-${w}x${h}-reception`, w);
    },
    STEPS_MS,
  );

/** Where focus is now: its region and test id. */
const focusNow = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    const region = a === null || a === document.body ? 'BODY' : a.closest('[data-testid="di-scan-home"]') ? 'HOME' : a.closest('[data-testid="di-scan-stage"]') ? 'STAGE' : a.closest('nav') ? 'RAIL' : 'OTHER';
    return { region, id: a?.getAttribute('data-testid') ?? a?.tagName ?? null, ref: a?.getAttribute('data-mission-ref') ?? null };
  });
/**
 * How many SEQUENTIAL presses of `key`, from wherever focus is NOW (never moved programmatically first), until focus is in Home (null: not within `max`).
 */
async function pressesToHomeFromHere(key: 'Tab' | 'Shift+Tab', max = 60): Promise<number | null> {
  for (let i = 1; i <= max; i++) {
    await p().keyboard.press(key);
    if ((await focusNow()).region === 'HOME') return i;
  }
  return null;
}
/** Escape back to the list, and where focus is then. */
async function escapeToList(): Promise<{ marks: Awaited<ReturnType<typeof marks>>; focus: Awaited<ReturnType<typeof focusNow>> }> {
  await p().keyboard.press('Escape');
  await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
  return { marks: await marks(), focus: await focusNow() };
}
/** Wait for Mission details after an open, then where focus is. */
async function afterOpen(): Promise<Awaited<ReturnType<typeof focusNow>>> {
  await p().waitForSelector(sel('di-scan-mission-title'));
  await p().waitForTimeout(300);
  return focusNow();
}

// The Lead's ruling (B), condition 3: ONE test asserting the SAME outcome at both widths, so the grammar cannot fork again. Every focus step here is a REAL
// user path: a pointer click, or SEQUENTIAL keys (Tab, Shift+Tab, Enter, Escape) from wherever the PRODUCT left focus; focus is never moved
// programmatically. The named target after opening is the screen's Back (`di-scan-back`, as at HEAD; Mission details has no page heading), at both
// widths, by both opening paths. Condition 2 (Tab follows the window's order) is asserted: the stops after opening are in the screen. Condition 1 (the
// cost of getting back to the list) is MEASURED from the product-placed focus and reported, not asserted.
test(
  'B2b ONE GRAMMAR: at 1280 (one pane) and 1440 (two panes) the same keys give the same states: opening by pointer or by Enter on the row puts focus on the screen\'s Back (named), Tab goes on INTO the screen, Escape closes with nothing marked and focus on the dismissed row',
  async () => {
    const seen: Array<Record<string, unknown>> = [];
    for (const [w, h] of [SIZES[0], SIZES[1]]) {
      await page?.close();
      await bootApp(w, h);
      const row = p().locator(sel('di-scan-row')).nth(1);
      const ref = (await row.getAttribute('data-mission-ref'))!;
      // 1. open by POINTER; Tab from where the product put focus
      await row.click();
      const pointerFocus = await afterOpen();
      const opened = await marks();
      const next: string[] = [];
      for (let i = 0; i < 3; i++) {
        await p().keyboard.press('Tab');
        next.push((await focusNow()).region);
      }
      const closed1 = await escapeToList();
      // 2. open by Enter on the row the product returned focus to (a sequential key, no programmatic focus), and close at once
      await p().keyboard.press('Enter');
      const enterFocus = await afterOpen();
      const closed2 = await escapeToList();
      // 2b. Back EXERCISED (the Lead, 2026-11-05: it was never activated before, so its result was unproven): open by Enter on the row, then Enter on Back,
      // which is where the product put focus
      await p().keyboard.press('Enter');
      const backFocus = await afterOpen();
      await p().keyboard.press('Enter');
      await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
      const closedByBack = { marks: await marks(), focus: await focusNow() };
      // 3. MEASURE (not part of the grammar claim): open by Enter again, then count Shift+Tab to Home from the product-placed Back. A measurement moves
      // focus where a person would, so nothing after it is asserted as the grammar; the next measurement starts from a fresh page.
      await p().keyboard.press('Enter');
      await afterOpen();
      const shiftTabToHome = await pressesToHomeFromHere('Shift+Tab');
      // 4. MEASURE: a fresh page, open by pointer, count Tab to Home from the product-placed Back
      await page?.close();
      await bootApp(w, h);
      await p().locator(sel('di-scan-row')).nth(1).click();
      await afterOpen();
      const tabToHome = await pressesToHomeFromHere('Tab');
      measured[`return to the list @${w}`] = {
        method: 'sequential keys from the product-placed focus after opening (Back); no programmatic focus',
        escape: 'ONE key (asserted below: nothing marked, focus on the dismissed row); it DISMISSES the docked Mission',
        back: 'ONE key, Enter on Back where opening left focus (asserted below, exercised); it DISMISSES the docked Mission',
        shiftTabToHome,
        tabToHome,
        note: w >= 1440 ? 'two panes: Home is beside the screen and in the Tab order' : 'one pane: Home is covered and out of the Tab order by design (null = not reachable by Tab, as intended)',
      };
      seen.push({
        opened: opened.stage === ref && opened.rows[0] === ref,
        focusAfterPointerOpen: pointerFocus.id,
        focusAfterEnterOpen: enterFocus.id,
        focusBeforeBack: backFocus.id,
        closedByBack: closedByBack.marks,
        focusOnRowAfterBack: closedByBack.focus.ref === ref,
        tabGoesInto: next,
        closed: [closed1.marks, closed2.marks],
        focusOnRowAfterEscape: [closed1.focus.ref === ref, closed2.focus.ref === ref],
      });
    }
    const none = { rows: [], stage: null };
    assert.deepEqual(
      seen[0],
      {
        opened: true,
        focusAfterPointerOpen: 'di-scan-back',
        focusAfterEnterOpen: 'di-scan-back',
        focusBeforeBack: 'di-scan-back',
        closedByBack: none,
        focusOnRowAfterBack: true,
        tabGoesInto: ['STAGE', 'STAGE', 'STAGE'],
        closed: [none, none],
        focusOnRowAfterEscape: [true, true],
      },
      'at 1280: both opening paths put focus on Back; Tab goes into the screen; each Escape closes with nothing marked and focus on the row',
    );
    assert.deepEqual(seen[1], seen[0], 'at 1440 the same keys give the same states (no fork between the modes)');
  },
  STEPS_MS,
);

/** Every text node inside the stage whose drawn size is off the scale, or a label (11 px) that is not uppercase and tracked. */
const offScale = () =>
  p().evaluate((scale) => {
    const stage = document.querySelector('[data-testid="di-scan-stage"]');
    if (stage === null) return [{ text: 'NO STAGE', px: 0, why: 'no stage' }];
    const bad: Array<{ text: string; px: number; why: string; cls: string }> = [];
    const walker = document.createTreeWalker(stage, NodeFilter.SHOW_TEXT);
    let count = 0;
    for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) {
      const text = (n.nodeValue ?? '').trim();
      if (text === '') continue;
      count += 1;
      const el = n.parentElement!;
      const cs = getComputedStyle(el);
      const px = Math.round(parseFloat(cs.fontSize) * 100) / 100;
      const cls = `${el.tagName.toLowerCase()}.${el.className}`.slice(0, 80);
      if (!scale.includes(px)) bad.push({ text: text.slice(0, 40), px, why: 'off the scale', cls });
      else if (px === 11) {
        const upper = cs.textTransform === 'uppercase' || text === text.toUpperCase();
        const tracked = text.length <= 1 || Math.abs(parseFloat(cs.letterSpacing) - 0.88) < 0.02;
        if (!upper || !tracked) bad.push({ text: text.slice(0, 40), px, why: `11 px but not a label (uppercase ${upper}, tracked ${tracked})`, cls });
      }
    }
    if (count === 0) bad.push({ text: 'NO TEXT', px: 0, why: 'the stage drew no text', cls: '' });
    return bad;
  }, SCALE);

const VIEWS: Array<[string, string, string]> = [
  ['Mission details (populated)', 'mission', '&di-scan-case=populated'],
  ['Code Diff', 'diff', ''],
  ['Comments', 'comments', ''],
  ['Missions (rail page)', 'missions', ''],
  ['Reception (rail page)', 'reception', '&di-scan-cast=v3'],
  ['New Mission (rail page)', 'new', ''],
];
for (const [w, h] of SIZES)
  test(
    `B2b THE RAMP (Q1) at ${w}x${h}: every text in the stage, in every view, is drawn at a step of the scale (11, 12, 13, 14, 18), and every 11 px text is a label`,
    async () => {
      const found: Record<string, unknown> = {};
      for (const [name, view, query] of VIEWS) {
        await page?.close();
        await bootLock(w, h, view, query);
        found[name] = await offScale();
        await shot(`b2b-${w}x${h}-ramp-${view}`, w);
      }
      measured[`ramp @${w}`] = found;
      for (const [name] of VIEWS) assert.deepEqual(found[name], [], `${name} @${w}: no text off the scale`);
    },
    STEPS_MS,
  );

test(
  'B2b THE CAP ((a)) at 1600x1000: Mission details\' prose is at most 70ch wide, where its card is wider than that',
  async () => {
    await bootLock(1600, 1000, 'mission', '&di-scan-case=populated');
    await p().locator(sel('di-scan-details-toggle')).click();
    const m = await p().evaluate(() => {
      const prose = [...document.querySelectorAll('[data-testid="di-scan-stage"] .di-scan-card p')].filter((el) => (el.textContent ?? '').trim().length > 80);
      const card = document.querySelector('[data-testid="di-scan-details"]')!;
      const probe = document.createElement('span');
      probe.textContent = '0';
      prose[0]!.appendChild(probe);
      const ch = probe.getBoundingClientRect().width;
      probe.remove();
      const cs = getComputedStyle(card);
      const inner = card.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return { count: prose.length, ch, cap: 70 * ch, widths: prose.map((el) => el.getBoundingClientRect().width), inner };
    });
    measured['cap @1600'] = m;
    assert.ok(m.count > 0, 'there is prose to cap');
    assert.ok(m.inner > m.cap + 10, `the card is wider than the cap (inner ${m.inner}, cap ${m.cap}), so the cap is not vacuous`);
    for (const width of m.widths) assert.ok(width <= m.cap + 1, `a prose block is ${width} px wide, at most 70ch (${m.cap})`);
  },
  STEPS_MS,
);

test(
  'B2b THE RAMP, the shared em value: New Mission\'s marker for an invisible character (`.di-mr-ctl`, 0.9em, shared with the request workspace) is MEASURED in the stage, and must be on the scale like every other text there',
  async () => {
    // a Recent ask holding U+202E (right-to-left override): the product shows it as a visible marker, never performs it
    overview = syntheticOverview([syntheticConversationSummary(SYNTHETIC_CONVERSATION_ID, 'SYNTHETIC ask with \u202E an override')]) as Record<string, unknown>;
    await bootApp(1440, 900);
    await p().click(sel('di-rail-new-orchestrator'));
    const marker = p().locator('[data-testid="di-scan-stage"] [data-testid="di-mr-control"]').first();
    await marker.waitFor({ timeout: 20_000 });
    const m = await marker.evaluate((el) => {
      const cs = getComputedStyle(el);
      const parent = getComputedStyle(el.parentElement!);
      return { text: el.textContent, px: parseFloat(cs.fontSize), parentPx: parseFloat(parent.fontSize) };
    });
    measured['di-mr-ctl in New Mission @1440'] = m;
    assert.match(m.text ?? '', /U\+202E/, 'the marker is drawn (the check is not vacuous)');
    assert.deepEqual(await offScale(), [], `every text in New Mission is on the scale (the marker draws at ${m.px} px inside ${m.parentPx} px)`);
  },
  STEPS_MS,
);

// The Lead, 2026-11-05: Escape and Back DISMISS the docked Mission; for a layout whose thesis is the list beside the detail, reaching the list must be cheap
// WITHOUT dismissing it. The deliberate path, MEASURED by real keys from where the product leaves focus: Shift+Tab from Back reaches "Go to the Mission
// list" (the screen's first stop, two panes only), Enter puts focus on the docked Mission's row, the Mission STAYS docked; Enter on that row returns focus
// to the screen's Back, still docked. In one pane the list is covered, and the link is not offered.
for (const [w, h] of SIZES)
  test(
    `B2b THE CHEAP PATH at ${w}x${h}: ${w >= 1440 ? 'from Back, Shift+Tab then Enter reaches the docked Mission\'s row WITHOUT dismissing it, and Enter on that row returns to Back' : 'one pane: no link to a covered list'}`,
    async () => {
      await bootApp(w, h);
      const row = p().locator(sel('di-scan-row')).nth(1);
      const ref = (await row.getAttribute('data-mission-ref'))!;
      await row.click();
      const start = await afterOpen();
      assert.equal(start.id, 'di-scan-back', 'opening left focus on Back');
      if (w < 1440) {
        assert.equal(await p().locator(sel('di-scan-to-list')).count(), 0, 'one pane: the link is not offered');
        return;
      }
      let keys = 0;
      await p().keyboard.press('Shift+Tab');
      keys += 1;
      const onLink = await focusNow();
      const shown = await p().evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const r = el.getBoundingClientRect();
        const stage = document.querySelector('[data-testid="di-scan-stage"]')!.getBoundingClientRect();
        const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
        return { inStage: r.top >= stage.top && r.left >= stage.left && r.right <= stage.right && r.width > 0, onTop: hit === el || el.contains(hit) };
      });
      await p().keyboard.press('Enter');
      keys += 1;
      const onRow = await focusNow();
      const stillDocked = await marks();
      await p().keyboard.press('Enter');
      const back = await focusNow();
      const stillDocked2 = await marks();
      measured[`cheap path to the list @${w}`] = { method: 'real keys from the product-placed Back', toList: keys, backToScreen: 1, onLink: onLink.id, onRow: onRow.ref, back: back.id };
      assert.equal(onLink.id, 'di-scan-to-list', 'Shift+Tab from Back reaches "Go to the Mission list"');
      assert.deepEqual(shown, { inStage: true, onTop: true }, 'the link is SHOWN while it has focus, inside the screen');
      assert.deepEqual({ region: onRow.region, ref: onRow.ref }, { region: 'HOME', ref }, 'Enter puts focus on the docked Mission\'s row in the list');
      assert.deepEqual(stillDocked, { rows: [ref], stage: ref }, 'the Mission STAYS docked');
      assert.equal(back.id, 'di-scan-back', 'Enter on the docked row returns focus to the screen\'s Back');
      assert.deepEqual(stillDocked2, { rows: [ref], stage: ref }, 'still docked');
      assert.equal(keys, 2, 'two keys to the list');
    },
    STEPS_MS,
  );

// Condition 2 of the spacing ruling: the LARGEST distortions of the mapping, 26 -> 32 (+23%, a section's top margin) and 40 -> 48 (+20%, Comments' side
// padding), MEASURED at every width: the drawn distance is the new step, and nothing overflows the screen or overlaps the block above.
for (const [w, h] of SIZES)
  test(
    `B2b LAYOUT STEPS at ${w}x${h}: the 26 -> 32 section separation and the 40 -> 48 Comments padding draw as the step, and break no composition`,
    async () => {
      await bootLock(w, h, 'mission', '&di-scan-case=populated');
      const sec = await p().evaluate(() => {
        const head = document.querySelector('[data-testid="di-scan-stage"] .di-scan-section-head')!;
        // the heading is the first child of its <section> (Delivery): the separation it makes is from the block BEFORE that section
        const prev = (head.closest('section') ?? head.parentElement!).previousElementSibling!;
        const stage = document.querySelector('[data-testid="di-scan-stage"] .di-scan-mission')!;
        const hr = head.getBoundingClientRect();
        return { marginTop: parseFloat(getComputedStyle(head).marginTop), gap: Math.round(hr.top - prev.getBoundingClientRect().bottom), overflowX: stage.scrollWidth - stage.clientWidth };
      });
      await shot(`b2b-${w}x${h}-layout-mission`, w);
      await page?.close();
      await bootLock(w, h, 'comments');
      const com = await p().evaluate(() => {
        const view = document.querySelector('[data-testid="di-scan-comments-view"]')!;
        const cs = getComputedStyle(view);
        return { left: parseFloat(cs.paddingLeft), right: parseFloat(cs.paddingRight), overflowX: view.scrollWidth - view.clientWidth };
      });
      await shot(`b2b-${w}x${h}-layout-comments`, w);
      measured[`layout steps @${w}`] = { sectionHead: sec, comments: com };
      assert.equal(sec.marginTop, 32, 'the section heading\'s top margin is the layout step 32 (was 26)');
      assert.ok(sec.gap >= 0, `the section heading does not overlap the block above (gap ${sec.gap})`);
      assert.ok(sec.overflowX <= 0, `Mission details does not overflow sideways (${sec.overflowX})`);
      assert.ok(com.left >= 32 && com.right === 48, `Comments' padding is the layout steps (left ${com.left}, right ${com.right}; was 28 / 40)`);
      assert.ok(com.overflowX <= 0, `Comments does not overflow sideways (${com.overflowX})`);
    },
    STEPS_MS,
  );
