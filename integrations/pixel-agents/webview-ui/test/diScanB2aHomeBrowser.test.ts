/**
 * Direction B, stage B2a (behaviour only; the docking geometry is B2b), RENDERED in Chrome. The real App (test/harness/di-app-scan.html), with the office as
 * the session-mismatch v2 FIXTURE in its labelled available-capacity variation and the request panel LABELLED SYNTHETIC. Not Brave evidence.
 *
 * What it pins, each by a real sequence, never by reasoning from the markup:
 *  - Home (the Mission list) stays MOUNTED while a screen covers it, and while covered it is out of reach: no Home control is reached by sequential Tab and
 *    none is in the accessibility tree; with nothing covering it, the same walk does reach it (the positive control);
 *  - the promise of "always mounted": the tab, the search words, the list's scroll position and the Needs-you filter survive a dock and dismiss round trip;
 *  - ONE click docks (Q8), and "selected" means "docked": a row carries aria-current only while its Mission is docked, and it and the rail's mark move as
 *    ONE state where the Mission HAS a rail entry (docking another moves both; dismissing clears both), and where it has none (the rail lists running
 *    Orchestrators, so a finished Mission has no entry) NO other rail entry is marked (Q8 one state, as narrowed by the Lead 2026-11-05);
 *  - dismissal by Escape puts focus on the dismissed Mission's own row (the Lead's dismissal rule);
 *  - the Swarm's summary line makes the roster discoverable: the line and its labelled toggle are in view and reached by Tab, and the toggle shows the members;
 *  - the app's pixel-lock entry (`?di-scan-lock=1`, DiOfficePanel) opens on Home with NOTHING selected: no preselected row, no card, and every row docks on
 *    one click (the Lead, 2026-11-05, item 1: the entry state is fixed, not carried).
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

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', syntheticOverview())));
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
    console.warn(`SKIPPED (B2a Home): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const view = () => p().locator(sel('di-scan-panel')).getAttribute('data-view');
const rows = () => p().locator(sel('di-scan-row'));

/** Home, at a window short enough that the list scrolls. */
async function boot(width = 1280, height = 560, query = ''): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${url}${query}`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
}

/** Sequential Tab, `presses` times: for each stop, whether it is inside Home (the Mission list). */
async function tabWalkInHome(presses: number): Promise<boolean[]> {
  const seen: boolean[] = [];
  for (let i = 0; i < presses; i++) {
    await p().keyboard.press('Tab');
    seen.push(await p().evaluate(() => document.querySelector('[data-testid="di-scan-home"]')?.contains(document.activeElement) === true));
  }
  return seen;
}
/**
 * Every mark, by the Mission's IDENTITY (its ref), never by its title: the fixture has many rows with the same title ("No goal reported"), so a title match
 * would pass with the wrong Mission marked. The rail entry, the list row and the docked stage each carry `data-mission-ref`.
 */
const marks = () =>
  p().evaluate(() => ({
    rows: [...document.querySelectorAll('[data-testid="di-scan-row"][aria-current]')].map((r) => `${r.getAttribute('data-mission-ref')}=${r.getAttribute('aria-current')}`),
    rail: [...document.querySelectorAll('[data-testid="di-rail-orchestrator"][aria-current]')].map((r) => `${r.getAttribute('data-mission-ref')}=${r.getAttribute('aria-current')}`),
    stage: document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-mission-ref') ?? null,
  }));
const railEntries = () => p().locator(sel('di-rail-orchestrator'));
const hasRailEntry = async (ref: string) => (await p().locator(`[data-testid="di-rail-orchestrator"][data-mission-ref="${ref}"]`).count()) === 1;
/**
 * Q8 one state, as NARROWED by the Lead (2026-11-05). The docked row and the stage always name the same Mission. Where that Mission has an ELIGIBLE rail
 * entry, the rail marks exactly that entry. Where it has none (the rail lists running Orchestrators, so a finished Mission is not in it, and must not be
 * added to make this pass), NO rail entry is marked: the failure guarded is the WRONG Mission highlighted in the rail. The case is stated by the caller
 * and checked, never inferred, so the narrowing cannot hide a one-state regression.
 */
async function expectDocked(ref: string, railEligible: boolean, what: string): Promise<void> {
  assert.equal(await hasRailEntry(ref), railEligible, `${what}: precondition, ${ref} ${railEligible ? 'HAS' : 'has NO'} rail entry`);
  if (!railEligible) assert.ok((await railEntries().count()) > 0, `${what}: the rail has entries, so "none marked" is not vacuous`);
  assert.deepEqual(
    await marks(),
    { rows: [`${ref}=true`], rail: railEligible ? [`${ref}=page`] : [], stage: ref },
    `${what}: the row and the stage name ${ref}; the rail marks ${railEligible ? 'its entry and no other' : 'NO entry'}`,
  );
}

test(
  'B2a: while a screen covers Home it stays MOUNTED, keeps its list scroll, and is out of reach: no Home control by sequential Tab, none in the accessibility tree; uncovered, the same walk reaches it',
  async () => {
    await boot();
    // the positive control first: uncovered, sequential Tab DOES reach Home, and the accessibility tree has its search box and tabs
    const uncovered = await tabWalkInHome(40);
    assert.ok(uncovered.some(Boolean), 'with nothing covering it, sequential Tab reaches Home');
    assert.match(await p().locator('body').ariaSnapshot(), /Search missions/, 'uncovered, Home is in the accessibility tree');
    // the list scrolled, then a Mission docked from the RAIL (a click there does not move the list)
    const list = p().locator(sel('di-missions'));
    await list.hover();
    await p().mouse.wheel(0, 120);
    await p().waitForTimeout(200);
    const scrolled = await list.evaluate((el) => el.scrollTop);
    assert.ok(scrolled > 0, `the list is scrolled (${scrolled})`);
    await p().locator(sel('di-rail-orchestrator')).first().click();
    await p().waitForSelector(sel('di-scan-mission-title'));
    assert.equal(await view(), 'mission');
    assert.equal(await p().locator(sel('di-scan-home')).count(), 1, 'Home is still MOUNTED while Mission details covers it');
    assert.equal(await rows().count() > 0, true, 'its rows are still in the document');
    // CHANGED IN round 11: this check was in the round-trip test, where a run with Home unmounted died on it before reaching the round trip at all
    const coveredScroll = await list.evaluate((el) => el.scrollTop);
    assert.equal(coveredScroll, scrolled, `the covered list keeps its scroll while covered (before ${scrolled}, covered ${coveredScroll})`);
    await p().locator(sel('di-scan-mission-title')).click();
    const covered = await tabWalkInHome(80);
    assert.deepEqual(covered.filter(Boolean), [], 'no sequential Tab stop is inside the covered Home');
    const tree = await p().locator('body').ariaSnapshot();
    assert.doesNotMatch(tree, /Search missions/, 'the covered Home is not in the accessibility tree (its search box)');
    assert.doesNotMatch(tree, /tab "Merged"/, 'the covered Home is not in the accessibility tree (its tabs)');
  },
  STEPS_MS,
);

test(
  'B2a: the promise of ALWAYS MOUNTED: the tab, the search words, the list scroll and the Needs-you filter survive a dock and dismiss round trip, and Escape puts focus on the dismissed row',
  async () => {
    await boot();
    // a search that keeps several rows, a list scrolled away from its top
    await p().fill(sel('di-scan-search'), 'goal');
    const shownBefore = await rows().count();
    assert.ok(shownBefore >= 2, `the search keeps several rows (${shownBefore})`);
    const list = p().locator(sel('di-missions'));
    const scrollable = await list.evaluate((el) => el.scrollHeight > el.clientHeight + 4);
    assert.equal(scrollable, true, 'precondition: the list scrolls in this window');
    await list.hover();
    await p().mouse.wheel(0, 120);
    await p().waitForTimeout(200);
    const scrollBefore = await list.evaluate((el) => el.scrollTop);
    assert.ok(scrollBefore > 0, `the list is scrolled (${scrollBefore})`);
    // dock a row that is WHOLLY in view, then dismiss it with Escape
    const inView = await list.evaluate((el) => {
      const l = el.getBoundingClientRect();
      return [...el.querySelectorAll('[data-testid="di-scan-row"]')].findIndex((r) => {
        const b = r.getBoundingClientRect();
        return b.top >= l.top && b.bottom <= l.bottom;
      });
    });
    assert.ok(inView >= 0, 'a row is wholly in view');
    const target = rows().nth(inView);
    const ref = (await target.getAttribute('data-mission-ref'))!;
    await target.click();
    await p().waitForSelector(sel('di-scan-mission-title'));
    // CHANGED IN round 11: nothing here reads the COVERED list (that is the first test's), so a run without a mounted Home still reaches the round trip
    await p().keyboard.press('Escape');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
    assert.equal(await p().inputValue(sel('di-scan-search')), 'goal', 'the search words survived');
    assert.equal(await rows().count(), shownBefore, 'the filtered rows are the same');
    const scrollAfter = await list.evaluate((el) => el.scrollTop);
    assert.equal(scrollAfter, scrollBefore, `the list scroll position survived the round trip (before ${scrollBefore}, after ${scrollAfter})`);
    assert.equal(await p().locator(sel('di-scan-tab-open')).getAttribute('aria-selected'), 'true', 'the tab survived');
    assert.equal(
      await p().evaluate(() => document.activeElement?.getAttribute('data-mission-ref') ?? document.activeElement?.getAttribute('data-testid') ?? null),
      ref,
      'Escape put focus on the dismissed Mission’s own row',
    );
    // the Needs-you filter and another tab survive a round trip too (the Mission opened from the rail, so the filtered list need not hold it)
    await p().fill(sel('di-scan-search'), '');
    await p().click(sel('di-scan-tab-archived'));
    // the filter is an item of the Options menu: choose it there, then READ its state from the item (open the menu, read, close it with Escape, which the menu consumes)
    const filterPressed = async () => {
      await p().click(sel('di-scan-options'));
      await p().waitForSelector(sel('di-scan-filter'));
      const pressed = await p().locator(sel('di-scan-filter')).getAttribute('aria-pressed');
      await p().keyboard.press('Escape');
      return pressed;
    };
    await p().click(sel('di-scan-options'));
    await p().click(sel('di-scan-filter'));
    assert.equal(await filterPressed(), 'true');
    await p().locator(sel('di-rail-orchestrator')).first().click();
    await p().waitForSelector(sel('di-scan-mission-title'));
    await p().keyboard.press('Escape');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
    assert.equal(await filterPressed(), 'true', 'the Needs-you filter survived');
    assert.equal(await p().locator(sel('di-scan-tab-archived')).getAttribute('aria-selected'), 'true', 'the archived tab survived');
  },
  STEPS_MS,
);

test(
  'B2a (Q8, Q4): ONE click docks; a row carries aria-current ONLY while its Mission is docked; for Missions WITH a rail entry the row and the rail move as ONE state: docking another moves both, dismissing clears both',
  async () => {
    await boot(1440, 900);
    assert.deepEqual(await marks(), { rows: [], rail: [], stage: null }, 'Home: nothing is marked and nothing is docked');
    const first = rows().first();
    const ref = (await first.getAttribute('data-mission-ref'))!;
    assert.ok(ref !== '', 'the row names its Mission');
    await first.click();
    await p().waitForSelector(sel('di-scan-mission-title'));
    assert.equal(await view(), 'mission', 'ONE click docked it (no select-then-open)');
    // the POSITIVE eligible case: the SAME identity, three ways (the docked stage, the one marked row, the one marked rail entry)
    await expectDocked(ref, true, 'docked from the list');
    // another Mission docked from the rail: both marks move together, to the identity the rail entry names
    const railItems = p().locator(sel('di-rail-orchestrator'));
    const n = await railItems.count();
    let otherRef: string | null = null;
    for (let i = 0; i < n && otherRef === null; i++) {
      const candidate = await railItems.nth(i).getAttribute('data-mission-ref');
      if (candidate !== ref && (await p().locator(`[data-testid="di-scan-row"][data-mission-ref="${candidate}"]`).count()) === 1) otherRef = candidate;
    }
    assert.ok(otherRef !== null, 'there is another Mission in the rail that the list also shows');
    await p().locator(`[data-testid="di-rail-orchestrator"][data-mission-ref="${otherRef}"]`).click();
    await p().waitForFunction((r) => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-mission-ref') === r, otherRef);
    await expectDocked(otherRef!, true, 'another Mission docked from the rail: both marks moved to it');
    // dismissed: both clear together
    await p().keyboard.press('Escape');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
    assert.deepEqual(await marks(), { rows: [], rail: [], stage: null }, 'dismissed: no row and no rail entry is marked, and nothing is docked');
  },
  STEPS_MS,
);

test(
  'B2a (Q8 condition, A9): the Swarm summary line makes the roster DISCOVERABLE: the line and its labelled toggle are in view and reached by Tab; the toggle shows the members',
  async () => {
    await boot(1440, 900);
    await rows().first().click();
    await p().waitForSelector(sel('di-scan-mission-title'));
    const status = p().locator(sel('di-scan-swarm-status'));
    const toggle = p().locator(sel('di-scan-swarm-toggle'));
    assert.equal(await status.isVisible(), true, 'the one summary line is shown');
    assert.match(await status.innerText(), /\d+ roles? listed/, 'it says how many roles there are');
    assert.equal(await toggle.isVisible(), true, 'the roster toggle is shown');
    assert.match(await toggle.innerText(), /roster/i, 'and is labelled as the roster');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'the roster starts closed');
    // CHANGED IN B2b: sequential Tab starts from where the PRODUCT leaves focus after opening (the docked screen's Back, the Lead's ruling (B)). This test
    // used to click the title first, believing that set Tab's starting point inside the stage; it did not (Tab started at the rail, at 1280 too). It
    // passed in B2a only because 40 presses happened to reach the toggle through the rail; in two panes Home's ~20 stops came first and it ran out. A
    // pass by accident of budget, not because the behaviour held, so the old green is not evidence of where Tab goes.
    assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? null), 'di-scan-back', 'opening left focus on the screen\'s Back');
    let reached = false;
    for (let i = 0; i < 40 && !reached; i++) {
      await p().keyboard.press('Tab');
      reached = await p().evaluate(() => document.activeElement?.getAttribute('data-testid') === 'di-scan-swarm-toggle');
    }
    assert.equal(reached, true, 'sequential Tab reaches the roster toggle');
    await p().keyboard.press('Enter');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true', 'Enter opens the roster');
    assert.ok((await p().locator(sel('di-scan-swarm-member')).count()) > 0, 'the members are shown');
    assert.equal(await p().locator(sel('di-scan-swarm-member')).first().isVisible(), true, 'and visible');
  },
  STEPS_MS,
);

test(
  'B2a (the fixture entry): the app\'s pixel-lock mode opens on Home with NOTHING selected (no row or rail entry marked, no card, nothing docked); a row WITH a rail entry and a row WITHOUT one each dock on ONE click, and no wrong rail entry is ever marked',
  async () => {
    await boot(1440, 900, '?di-scan-lock=1');
    await p().waitForSelector('[data-testid="di-scan-row"][data-mission-ref^="lock-"]');
    assert.deepEqual(await marks(), { rows: [], rail: [], stage: null }, 'the entry: nothing marked, nothing docked');
    assert.equal(await p().locator('[data-testid="di-scan-detail"]').count(), 0, 'no inline card is drawn');
    // one Home row of each case, each found and stated, never assumed
    const listed = await rows().evaluateAll((els) => els.map((e) => e.getAttribute('data-mission-ref')!));
    const inRail = await railEntries().evaluateAll((els) => els.map((e) => e.getAttribute('data-mission-ref')!));
    const eligible = listed.find((r) => inRail.includes(r));
    const ineligible = listed.find((r) => !inRail.includes(r));
    assert.ok(eligible !== undefined, `a Home row whose Mission HAS a rail entry (listed ${listed.join(', ')}; rail ${inRail.join(', ')})`);
    assert.ok(ineligible !== undefined, `a Home row whose Mission has NO rail entry (a finished Mission; the rail lists running Orchestrators)`);
    for (const [ref, railEligible] of [
      [eligible!, true],
      [ineligible!, false],
    ] as const) {
      await p().locator(`[data-testid="di-scan-row"][data-mission-ref="${ref}"]`).click();
      await p().waitForFunction((r) => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-mission-ref') === r, ref, { timeout: 10_000 });
      await expectDocked(ref, railEligible, `${ref} docked on ONE click`);
      await p().click(sel('di-scan-back'));
      await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list');
      assert.deepEqual(await marks(), { rows: [], rail: [], stage: null }, `${ref} dismissed: nothing marked`);
    }
  },
  STEPS_MS,
);

// B2a-PA2 (post-acceptance, approved by the Lead 2026-11-05): B2a's covered Home kept the clicked row focused until the browser dropped focus to the page
// body (verification/b/b2a-focus-after-open-probe.log: r11 BODY, HEAD di-scan-back). The claim's assertion is the focused element after opening.
// Three DIFFERENT user paths, named for what each is (the Lead, 2026-11-05): a pointer click; PROGRAMMATIC focus on the row then Enter (which proves
// nothing about reaching the row by keyboard); and SEQUENTIAL Tab from the page's start to the row, then Enter (real traversal). Each asserts the SAME
// named target: the screen's Back (`di-scan-back`), as at HEAD; Mission details has no page heading.
const PA2_PATHS = ['pointer', 'programmatic focus on the row, then Enter', 'sequential Tab to the row, then Enter'] as const;
for (const how of PA2_PATHS)
  test(
    `B2a-PA2 (AC-UX-07): at 1280 (one pane), opening a Mission from a Home row by ${how} puts focus on the new screen's Back (named), never on the page body`,
    async () => {
      await boot(1280, 800);
      const row = rows().first();
      if (how === 'pointer') await row.click();
      else if (how === 'programmatic focus on the row, then Enter') {
        await row.focus();
        await p().keyboard.press('Enter');
      } else {
        let onRow = false;
        for (let i = 0; i < 80 && !onRow; i++) {
          await p().keyboard.press('Tab');
          onRow = await p().evaluate(() => document.activeElement?.getAttribute('data-testid') === 'di-scan-row');
        }
        assert.equal(onRow, true, 'sequential Tab reached a Home row');
        await p().keyboard.press('Enter');
      }
      await p().waitForSelector(sel('di-scan-mission-title'));
      await p().waitForTimeout(300);
      const focused = await p().evaluate(() => (document.activeElement === document.body ? 'BODY' : (document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null)));
      assert.equal(focused, 'di-scan-back', `focus after opening by ${how}: ${focused}`);
    },
    STEPS_MS,
  );
