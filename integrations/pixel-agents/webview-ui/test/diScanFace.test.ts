/**
 * The PRODUCT face (the scan panel and its navigation rail) through the ACTUAL App, in a real browser, over the shipped
 * living metropolis and the labelled FIXTURE office state `di-office-state-transitions-v2.FIXTURE.json` (one reception
 * entry, drawn agents). Integration over source (Vite's dev server), not the production package; nothing here is live.
 *
 * Every other App browser test drives the operational face (di-app.html), which has no rail, so this is where pass A's
 * layout is checked against the WORLD it must not regress:
 *  - the rail sits at the window's left edge and the world starts at the rail's edge; the world's zoom and view controls
 *    are drawn whole beside it, with nothing painted over them;
 *  - clicking a drawn agent in the world still selects it (the world moved right; its hit-testing must move with it);
 *  - the rail's Reception count is the number the Reception header states (AC-4.7-16);
 *  - Reception has ONE home: the rail. Nothing else in the panel chrome, and nothing in the world (the desk and the
 *    receptionist included), opens the inbox (CONTROL-MAP "Reception left-rail only", D-10);
 *  - a Mission opened from the rail shows its details beside the rail, Herdr is disabled, and no in-app terminal exists.
 * Presentation and navigation only: no action route is exercised, because none is wired at this stage.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test
 * is skipped with an explicit message.
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
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { DI_PANEL_WIDTH_PX, DI_WORLD_SCAN_CONTROLS_MIN_PX } from '../src/constants.ts';
import { clickAcrossWorld } from './helpers/worldClickProbe.ts';

const STEPS_MS = 120_000;
const RAIL_PX = 200; // DI_RAIL_WIDTH_PX, as the design draws it (diRailLayout.test.ts pins the constant)
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LIVE = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { states: Record<'live', DiOfficeState> }
).states.live;
/** The running version, as the server reports it in settingsLoaded (package.json). */
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const metropolis = fs.readFileSync(
  path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'),
  'utf-8',
);

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: WebSocket | null = null;
/** Every message the page has sent on its socket since the last boot. */
const received: Array<Record<string, unknown>> = [];
let resolveConnected: (ws: WebSocket) => void = () => undefined;

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
        const message = JSON.parse(String(data)) as Record<string, unknown>;
        // everything the page sends is kept, so a test can answer a request the real app made (the Herdr control's, for one) and can say what was NOT sent
        received.push(message);
        if (message.type === 'webviewReady') {
          socket = ws;
          resolveConnected(ws);
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
}, STEPS_MS);

afterAll(async () => {
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (scan face): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
type Box = { x: number; y: number; width: number; height: number };
const box = async (selector: string): Promise<Box> => {
  const b = await p().locator(selector).first().boundingBox();
  assert.ok(b, `${selector} is drawn`);
  return b;
};

/** Open the App on the scan face at 1440 x 900, hand it the shipped world and the fixture, and wait until it is drawn. */
async function boot(options: { fakeClock?: boolean; seed?: number } = {}): Promise<void> {
  received.length = 0;
  const connected = new Promise<WebSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  // a test whose behaviour depends on TIME (the hover grace) installs Playwright's fake clock BEFORE the page loads; time then
  // flows normally until the test pauses it, and from then on moves only when the test advances it (runFor)
  if (options.fakeClock) await page.clock.install();
  // a test whose outcome depends on WHERE the agents sit (their seats are drawn at random) seeds Math.random before the page loads,
  // so the layout is the same every run (the same mulberry32 as homeAnchor.test.ts)
  if (options.seed !== undefined)
    await page.addInitScript((seed) => {
      let a = seed >>> 0;
      Math.random = () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    }, options.seed);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  // what the real server sends after webviewReady: the provider's capabilities (Claude's, server/src/providers/hook/claude/
  // claude.ts), so a Task tool start is a sub-agent here exactly as in the product
  socket!.send(
    JSON.stringify({
      type: 'providerCapabilities',
      readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'],
      subagentToolNames: ['Task', 'Agent'],
    }),
  );
  // ...and its settings, with the running version (a returning person who has seen it: no update notice)
  socket!.send(
    JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }),
  );
  socket!.send(JSON.stringify(LIVE));
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
  await page.waitForFunction(
    () => {
      const office = (
        window as unknown as { __diHarnessOffice?: { characters: Map<number, unknown> } }
      ).__diHarnessOffice;
      return office !== undefined && office.characters.size >= 4;
    },
    undefined,
    { timeout: 60_000 },
  );
  await p().waitForSelector('[data-testid="di-rail"]');
  // (pass C item 5: boot does NOT wait for the world's disclosure; a test that needs it asserts it is there)
}
/** Open the world's controls (the disclosure at the world's top-right), as a person would; it must already be drawn. */
async function openWorldControls(): Promise<void> {
  assert.equal(await p().locator('[data-testid="di-world-disclosure"]').count(), 1, 'the world disclosure is drawn');
  if ((await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded')) !== 'true')
    await p().click('[data-testid="di-world-disclosure"]');
  await p().waitForSelector('[data-testid="world-zoom-buttons"]');
}

test(
  'NON-REGRESSION: the rail sits at the left edge, the world starts at its edge, and the world controls are drawn whole beside it',
  async () => {
    await boot();
    // pass C item 5: the controls are behind the world's disclosure; opened, they are drawn whole as before
    await openWorldControls();
    const rail = await box('[data-testid="di-rail"]');
    assert.equal(rail.x, 0);
    assert.equal(rail.width, RAIL_PX);
    const canvas = await box('canvas');
    assert.ok(
      canvas.x >= RAIL_PX - 0.5,
      `the world starts at the rail's edge (canvas at ${canvas.x})`,
    );
    const panel = await box('[data-testid="di-office-panel"]');
    assert.ok(canvas.x + canvas.width <= panel.x + 0.5, 'and ends at the panel');
    for (const selector of [
      '[data-testid="world-zoom-buttons"]',
      '[data-testid="world-view-controls"]',
    ]) {
      const b = await box(selector);
      assert.ok(b.x >= RAIL_PX, `${selector} is right of the rail (x ${b.x})`);
      // nothing paints over it: the element at its centre is inside it
      const own = await p().evaluate(
        ([sel, x, y]) => {
          const hit = document.elementFromPoint(x as number, y as number);
          return hit !== null && (document.querySelector(sel as string)?.contains(hit) ?? false);
        },
        [selector, b.x + Math.min(b.width, 40) / 2, b.y + Math.min(b.height, 40) / 2] as const,
      );
      assert.ok(own, `${selector} is not covered`);
    }
  },
  STEPS_MS,
);

test(
  'NON-REGRESSION: clicking a drawn agent in the world still selects it beside the rail',
  async () => {
    await boot();
    type Office = {
      characters: Map<number, { isReceptionist?: boolean }>;
      selectedAgentId: number | null;
      cameraFollowId: number | null;
    };
    const ids = await p().evaluate(() =>
      [
        ...(
          window as unknown as { __diHarnessOffice: Office }
        ).__diHarnessOffice.characters.entries(),
      ]
        .filter(([id, ch]) => id > 0 && ch.isReceptionist !== true)
        .map(([id]) => id),
    );
    const clear = () =>
      p().evaluate(() => {
        const o = (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice;
        o.selectedAgentId = null;
        o.cameraFollowId = null;
      });
    const selectedNow = () =>
      p().evaluate(
        () =>
          (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice.selectedAgentId,
      );
    /**
     * Where the character stands NOW, from its own label (drawn at the character's projected position only while it is
     * selected): the point under the label's centre. Read fresh before every click, because idle characters wander.
     */
    const standsAt = async (id: number) => {
      await p().evaluate((i) => {
        (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice.selectedAgentId = i;
      }, id);
      const b = await p()
        .locator(`[data-testid="agent-overlay"][data-agent-id="${id}"]`)
        .first()
        .boundingBox({ timeout: 2_000 })
        .catch(() => null);
      await clear();
      return b ? { x: b.x + b.width / 2, y: b.y + b.height } : null;
    };
    /** A point a person could click on the world itself: right of the rail, left of the panel, the canvas on top. */
    const onWorld = (x: number, y: number) =>
      x > RAIL_PX + 8 &&
      x < 1440 - DI_PANEL_WIDTH_PX &&
      y > 8 &&
      y < 892 &&
      p().evaluate(([xx, yy]) => document.elementFromPoint(xx, yy)?.tagName === 'CANVAS', [
        x,
        y,
      ] as const);

    let target: number | null = null;
    let hit = false;
    search: for (let round = 0; round < 3; round++)
      for (const id of ids)
        for (const dy of [6, 12, 18, 24, 30, 36]) {
          const at = await standsAt(id);
          if (!at || !(await onWorld(at.x, at.y + dy))) continue;
          target = id;
          await p().mouse.click(at.x, at.y + dy);
          if ((await selectedNow()) === id) {
            hit = true;
            break search;
          }
          await clear();
        }
    assert.ok(target !== null, 'an agent stands in the world beside the rail');
    assert.ok(hit, `a real click on agent ${target} selected it`);
    // pass C (AC-4.4-26): a click on a Mission's agent also opens its Mission over the world; Back returns Home, where the
    // control click below must land on the world again
    if ((await stageMission()) !== null) await backHome();

    // CONTROL (the probe can fail): the same click 200 px to the right, the rail's width, does not select that agent,
    // so a hit-test that ignored the world's shift would have been caught above
    await clear();
    let controlled = false;
    for (const dy of [12, 18, 24, 30]) {
      const at = await standsAt(target!);
      if (!at || !(await onWorld(at.x + RAIL_PX, at.y + dy))) continue;
      await p().mouse.click(at.x + RAIL_PX, at.y + dy);
      assert.notEqual(await selectedNow(), target, 'a click a rail-width away does not select it');
      controlled = true;
      await clear();
      break;
    }
    assert.ok(controlled, 'the control click landed on the world');
  },
  STEPS_MS,
);

// Superseded in pass B (lead Q2, 2026-10-26): on the scan face the rail opens the FULL-PAGE Reception, not the inbox
// dialog, so the header the badge must equal is the page's. The page and the badge read one source (AC-4.7-16).
// Superseded again in pass C (lead, AC-4.7-16 UNDER UNCERTAINTY, 2026-10-26): agreement extends to the uncertainty, not
// only the digits. This office's catalog does not report clarification or delivery decisions (required coverage), so its
// count is a FLOOR: the badge must read "N+" and the header "At least N ...; more may", the same N, with the reason said.
test(
  'AC-4.7-16: the rail Reception count and the full-page Reception header express the same state: here a floor, "N+" and "At least N"',
  async () => {
    await boot();
    const badge = (await p().locator('[data-testid="di-rail-reception-count"]').innerText()).trim();
    const floor = /^(\d+)\+$/.exec(badge);
    assert.ok(floor, `the badge is a floor "N+": ${badge}`);
    const n = Number(floor[1]);
    assert.ok(n > 0, 'some are known to need attention');
    await p().click('[data-testid="di-open-reception"]');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="reception"]');
    const header = (await p().locator('[data-testid="di-reception-count"]').innerText()).trim();
    assert.equal(header, `At least ${n} ${n === 1 ? 'needs' : 'need'} attention; more may`);
    assert.match(
      (await p().locator('[data-testid="di-reception-doubt"]').innerText()).trim(),
      /Some things could not be checked: [^.]*questions for you/,
    );
    // the page opening moved neither number
    assert.equal(
      (await p().locator('[data-testid="di-rail-reception-count"]').innerText()).trim(),
      badge,
    );
  },
  STEPS_MS,
);

/** The next diHerdrRequest of this op the page sent after index `after` of `received`; fails if none comes. */
async function herdrRequestAfter(op: 'status' | 'launch', after: number): Promise<Record<string, unknown>> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const found = received.slice(after).find((m) => m.type === 'diHerdrRequest' && m.op === op);
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`the page sent no diHerdrRequest ${op}`);
}
const herdrAnswer = (over: Record<string, unknown> = {}) => ({
  agent: 'he-exec1',
  // the shape the native gate faces: a healthy bound task, whose role is found in the team roster
  identityBasis: 'team_roster_session_confirmed',
  workspaceId: 'w6V',
  // the window the launcher will ask for, derived by the office from Herdr's snapshot (A12): said as a requirement
  windowMinCols: 190,
  windowMinRows: 49,
  workspaceFocus: 'not_performed',
  matchedPaneId: 'w6V:p3',
  paneFocus: 'not_performed',
  terminal: 'terminal',
  terminalLabel: 'Terminal',
  terminalState: null,
  ...over,
});

test(
  'A4: a Mission opened from the rail shows its details beside the rail; Herdr is enabled by the office and asks it to open a terminal, and no in-app terminal exists',
  async () => {
    await boot();
    await p().click('[data-testid="di-rail-orchestrator"]');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    const stage = await box('[data-testid="di-scan-stage"]');
    assert.equal(stage.x, RAIL_PX);
    const missionRef = await p().locator('[data-testid="di-scan-stage"]').getAttribute('data-mission-ref');
    assert.ok(missionRef, 'the stage names its Mission');

    // THE REAL HOOK, AGAINST A STAND-IN OFFICE. Opening the Mission asks the office whether a launch is available: a status, which is a read.
    const status = await herdrRequestAfter('status', 0);
    assert.deepEqual(status, { type: 'diHerdrRequest', seq: status.seq, op: 'status', missionRef });
    // until the office answers, Herdr is disabled and says it is checking: nothing has been assumed
    assert.equal(await p().locator('[data-testid="di-scan-herdr"]').isDisabled(), true);
    assert.match((await p().locator('[data-testid="di-scan-herdr-reason"]').innerText()).trim(), /Checking whether Herdr can open for this Mission/);
    assert.equal(received.filter((m) => m.type === 'diHerdrRequest' && m.op === 'launch').length, 0, 'no launch was sent before the office said one is available');

    // the office says a launch is available: Herdr is enabled and says what a click does
    socket!.send(JSON.stringify({ type: 'diHerdrReply', seq: status.seq, op: 'status', state: 'ok', problem: null, reason: null, answer: herdrAnswer() }));
    await p().waitForFunction(() => !(document.querySelector('[data-testid="di-scan-herdr"]') as HTMLButtonElement).disabled);
    const available = (await p().locator('[data-testid="di-scan-herdr-reason"]').innerText()).trim();
    assert.match(available, /Opens Terminal on this Mission's herd\. It first asks Herdr to focus workspace w6V, and types nothing into any pane or terminal\./);
    assert.doesNotMatch(available, /herdctl|does not start it/);
    // the basis that vouched is on the page as what it is: a roster confirmation, not a runtime-identity verification
    assert.match(available, /found in the catalog's team roster with its recorded and live sessions equal \(a roster confirmation, not a runtime-identity verification\)/);
    assert.doesNotMatch(available, /runtime identity for the role is verified/);
    // the window requirement is on the page as a requirement with its reason, and nothing says the window got there
    assert.match(available, /Herdr sizes its shared session to its smallest client, so the window is asked to grow to at least 190 x 49 cells, and Herdr is started in it only if the window really is that large/);
    assert.doesNotMatch(available, /was resized|is large enough|now fits/i);

    // the click sends ONE closed launch request, and nothing else
    const before = received.length;
    await p().click('[data-testid="di-scan-herdr"]');
    const launch = await herdrRequestAfter('launch', before);
    assert.deepEqual(launch, { type: 'diHerdrRequest', seq: launch.seq, op: 'launch', missionRef });
    assert.equal(await p().locator('[data-testid="di-scan-herdr"]').isDisabled(), true, 'disabled while the launch is in flight');

    // the office's refusal is shown with its real words, and nothing is claimed
    const refusal = 'Nothing was opened: Workspace focus was not established, and Herdr\'s call failed, so nothing is known to have moved. The launcher was written at /x/herdr-attach-a1b2.command and was not used.';
    socket!.send(JSON.stringify({ type: 'diHerdrReply', seq: launch.seq, op: 'launch', state: 'refused', problem: 'focus_not_established', reason: refusal, answer: null }));
    await p().waitForFunction((words) => document.querySelector('[data-testid="di-scan-herdr-reason"]')?.textContent?.includes(words) === true, 'Nothing was opened: Workspace focus was not established');
    assert.equal((await p().locator('[data-testid="di-scan-herdr-reason"]').innerText()).includes('The launcher was written at /x/herdr-attach-a1b2.command'), true);

    // a second click, and a success: the words are what was established and no more
    const again = received.length;
    await p().click('[data-testid="di-scan-herdr"]');
    const second = await herdrRequestAfter('launch', again);
    socket!.send(
      JSON.stringify({ type: 'diHerdrReply', seq: second.seq, op: 'launch', state: 'ok', problem: null, reason: null, answer: herdrAnswer({ workspaceFocus: 'focused', terminalState: 'opened' }) }),
    );
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr-reason"]')?.textContent?.includes('Herdr reported workspace w6V focused') === true);
    const done = (await p().locator('[data-testid="di-scan-herdr-reason"]').innerText()).trim();
    assert.match(done, /Terminal was asked to open the Herdr launcher/);
    assert.match(done, /at least 190 x 49 cells/);
    assert.doesNotMatch(done, /attach|keyboard|typing works/i);

    // everything the Herdr control ever sent was a status or a launch: there is no message that types, prompts or sends a key
    const herdrOps = received.filter((m) => m.type === 'diHerdrRequest').map((m) => m.op);
    assert.deepEqual([...new Set(herdrOps)].sort(), ['launch', 'status']);
    assert.equal(received.filter((m) => /key|input|prompt|text/i.test(String(m.type))).length, 0);
    const terminal = await p().evaluate(
      () =>
        document.querySelectorAll(
          '.di-scan-herdr-view, .di-scan-pane, .di-scan-panes, [class*="di-scan-term"]',
        ).length +
        (document.body.innerText.includes('Ask Codex') ? 1 : 0) +
        (document.body.innerText.includes('herdr@') ? 1 : 0),
    );
    assert.equal(terminal, 0);
    // DI World returns Home
    await p().click('[data-testid="di-rail-home"]');
    await p().waitForSelector('[data-testid="di-scan-stage"]', { state: 'detached' });
  },
  STEPS_MS,
);

test(
  'SINGLE HOME (CONTROL-MAP locked rules, D-10, lead Q2): the rail opens the full-page Reception, the dialog is unreachable on the scan face, focus returns to the rail entry, and nothing in the world opens it',
  async () => {
    await boot();
    // every control in the panel chrome (the rail and the missions panel) that names Reception: exactly the rail entry
    const named = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-office-panel"] button')]
        .filter((b) => /reception/i.test(`${b.innerText} ${b.getAttribute('aria-label') ?? ''}`))
        .map((b) => ({
          testid: b.getAttribute('data-testid'),
          inRail: b.closest('[data-testid="di-rail"]') !== null,
        })),
    );
    assert.deepEqual(named, [{ testid: 'di-open-reception', inRail: true }]);

    // Superseded in pass B (lead Q2): the rail entry opened the inbox DIALOG. On the scan face it now opens the
    // FULL-PAGE Reception beside the rail, the dialog is not reachable at all, and Esc returns focus to the rail entry.
    const railEntry = '[data-testid="di-rail"] [data-testid="di-open-reception"]';
    await p().click(railEntry);
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="reception"]');
    await p().waitForSelector('[data-testid="di-reception-page"]');
    assert.equal(await p().getAttribute(railEntry, 'aria-current'), 'page');
    const stage = await box('[data-testid="di-scan-stage"]');
    assert.equal(stage.x, RAIL_PX, 'the page sits beside the rail, full height');
    assert.equal(
      await p().locator('[data-testid="di-reception-inbox"], [role="dialog"]').count(),
      0,
      'no inbox dialog opened',
    );
    // and nothing else on the scan face reaches the dialog: every control that names Reception, while the page is open
    const others = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLButtonElement>('button')].filter(
        (b) =>
          !b.disabled &&
          /reception/i.test(`${b.innerText} ${b.getAttribute('aria-label') ?? ''}`) &&
          b.getAttribute('data-testid') !== 'di-open-reception',
      ).length,
    );
    assert.equal(others, 0, 'no second Reception control exists on the scan face');
    // focus moves INTO the page first, so the return below is the page's own doing (a mutation that dropped the
    // return stayed green while focus had never left the rail entry)
    await p().click('[data-testid="di-reception-filter-all"]');
    assert.equal(
      await p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? null),
      'di-reception-filter-all',
      'focus is inside the Reception page before Esc',
    );
    await p().keyboard.press('Escape');
    await p().waitForSelector('[data-testid="di-scan-stage"]', { state: 'detached' });
    assert.equal(await p().locator('[data-testid="di-reception-inbox"]').count(), 0);
    assert.ok(
      await p().evaluate((s) => document.activeElement?.matches(s) ?? false, railEntry),
      'focus returns to the rail entry',
    );

    // nothing outside the chrome names Reception, and no desk target is drawn in the world
    const outside = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('button, [role="button"], a[href]')]
        .filter(
          (el) =>
            el.closest('[data-testid="di-office-panel"]') === null &&
            /reception/i.test(
              `${el.innerText} ${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`,
            ),
        )
        .map((el) => el.getAttribute('data-testid') ?? el.tagName),
    );
    assert.deepEqual(outside, []);
    assert.equal(await p().locator('[data-testid="di-reception-target"]').count(), 0);

    // real clicks across the whole visible world, between the rail and the panel, never open the inbox
    const probe = await clickAcrossWorld(
      p(),
      // DERIVED (pass C): the world ends at the panel's edge, wherever the panel's width moves
      { left: RAIL_PX, top: 0, right: 1440 - DI_PANEL_WIDTH_PX, bottom: 900 },
      24,
      // what each click did is read from the app's selection, not from a DOM sample (see helpers/worldClickProbe.ts)
      { opensMission: (agent) => agent !== null && MISSION_AGENTS.has(agent) },
    );
    assert.ok(probe.clicks > 400, `the world was probed (${probe.clicks} clicks)`);
    // every click probed the WORLD (pass C: a Mission opened by a click is undone before the next one)
    assert.equal(probe.worldClicks, probe.clicks, `every probe click landed on the world (${probe.worldClicks} of ${probe.clicks})`);
    console.info(`WORLD PROBE (%s): %d clicks, %d on the world, %d opened a Mission, the first at click %s`, 'scan face', probe.clicks, probe.worldClicks, probe.missionsOpened, String(probe.firstMissionAt));
    assert.equal(probe.inboxOpened, false, 'no click in the world opened the inbox');
  },
  STEPS_MS,
);

test(
  'the rail keeps its controls whole when there are many Orchestrators: + New Orchestrator keeps its full height, Settings stays in the window, and the Orchestrators scroll instead',
  async () => {
    await boot();
    const count = await p().locator('[data-testid="di-rail-orchestrator"]').count();
    assert.ok(count >= 15, `the fixture lists many Orchestrators (${count})`);
    const newButton = await box('[data-testid="di-rail-new-orchestrator"]');
    assert.equal(Math.round(newButton.height), 40, 'the primary action is not squashed');
    const reception = await box('[data-testid="di-open-reception"]');
    assert.ok(reception.height >= 36, `Reception keeps its height (${reception.height})`);
    const settings = await box('[data-testid="di-rail-settings"]');
    assert.ok(
      settings.y + settings.height <= 900,
      `Settings stays inside the window (bottom ${settings.y + settings.height})`,
    );
    const workspace = await box('[data-testid="di-rail-missions"]');
    assert.ok(
      workspace.y + workspace.height <= settings.y,
      'the Workspace entries sit above Settings, in the window',
    );
    // the Orchestrators list is what scrolls
    const scroll = await p().evaluate(() => {
      const list = document.querySelector<HTMLElement>('[data-testid="di-rail-orchestrators"]');
      return list
        ? {
            scrolls: list.scrollHeight > list.clientHeight,
            overflowY: getComputedStyle(list).overflowY,
          }
        : null;
    });
    assert.deepEqual(scroll, { scrolls: true, overflowY: 'auto' });
  },
  STEPS_MS,
);

test(
  'B-E1: in fixture mode every page and stage carries the fixture label, wherever the dock puts the world\'s own label (a stage covers it)',
  async () => {
    await boot();
    socket!.send(JSON.stringify({ ...LIVE, fixtureMode: true }));
    /** Every fixture label in the window that is on screen and topmost at its centre (not covered). */
    const visibleLabels = () =>
      p().evaluate(() =>
        [
          ...document.querySelectorAll<HTMLElement>(
            '[data-testid="di-fixture-banner-in-stage"], [data-testid="di-fixture-banner-in-panel"], [data-testid="di-fixture-banner"]',
          ),
        ]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.bottom <= 0 || r.top >= window.innerHeight) return false;
            const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return top !== null && (top === el || el.contains(top));
          })
          .map((el) => el.getAttribute('data-testid')),
      );
    for (const width of [1440, 900]) {
      await p().setViewportSize({ width, height: 900 });
      for (const open of ['[data-testid="di-rail-missions"]', '[data-testid="di-open-reception"]', '[data-testid="di-rail-new-orchestrator"]']) {
        await p().click(open);
        await p().waitForSelector('[data-testid="di-scan-stage"]');
        // exactly ONE label is visible: the stage's own; the world's floating label and the panel's in-panel label are
        // both under the stage (never a duplicate in a capture)
        assert.deepEqual(await visibleLabels(), ['di-fixture-banner-in-stage'], `${width} ${open}`);
      }
      // a record, not an assertion: which placement of the world's own label this width produced
      const present = await p().evaluate(() => ({
        inPanel: document.querySelector('[data-testid="di-fixture-banner-in-panel"]') !== null,
        floating: document.querySelector('[data-testid="di-fixture-banner"]') !== null,
      }));
      console.info(`FIXTURE LABEL PLACEMENT at ${width}x900: ${JSON.stringify(present)}`);
    }
    await p().setViewportSize({ width: 1440, height: 900 });
    for (const open of ['[data-testid="di-rail-missions"]', '[data-testid="di-open-reception"]', '[data-testid="di-rail-new-orchestrator"]']) {
      await p().click(open);
      await p().waitForSelector('[data-testid="di-scan-stage"]');
      const banner = await p().evaluate(() => {
        const el = document.querySelector('[data-testid="di-fixture-banner-in-stage"]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { text: el.textContent, topmost: top !== null && (top === el || el.contains(top)), inView: r.top >= 0 && r.bottom <= window.innerHeight };
      });
      assert.ok(banner, `${open}: the stage carries the fixture label`);
      assert.match(banner.text ?? '', /^FIXTURE/);
      assert.equal(banner.topmost, true, `${open}: nothing covers it`);
      assert.equal(banner.inView, true, `${open}: it is on screen`);
    }
  },
  STEPS_MS,
);

// ── AC-4.4-26 (pass C): clicking an agent or its Mission label opens THAT Mission; Back returns Home with the world as it was
// The fixture draws TWO Missions (characters 1-4 and 5-8) and runtime agents (9-16, never assigned to a Mission by their
// contract). Before each open the OTHER Mission is the selected Home row, so "open the selected row" cannot pass.
const HERD = 'herd:20261017-150121-aaaaaa@5a4574665441';
const CORE = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
// tried in this order: NOT the first drawn member first, since a chosen Home row moves the world's selection to its Mission's
// first drawn member (1 or 5); a click that kept the clicked agent selected is then told apart from that jump
const OF: Record<string, number[]> = { [HERD]: [2, 3, 4, 1], [CORE]: [6, 7, 8, 5] };
/** The characters that belong to a Mission (a click on one opens its Mission); 9 to 16 are runtime agents and open nothing. */
const MISSION_AGENTS = new Set([...OF[HERD]!, ...OF[CORE]!]);
type WorldOffice = {
  characters: Map<number, { isReceptionist?: boolean; state?: string; matrixEffect?: string | null }>;
  selectedAgentId: number | null;
  cameraFollowId: number | null;
  addSubagent: (parent: number, tool: string) => number;
};
const select = (id: number | null) =>
  p().evaluate((i) => {
    const o = (window as unknown as { __diHarnessOffice: WorldOffice }).__diHarnessOffice;
    o.selectedAgentId = i;
    o.cameraFollowId = null;
  }, id);
const selectedNow = () =>
  p().evaluate(() => {
    const o = (window as unknown as { __diHarnessOffice: WorldOffice }).__diHarnessOffice;
    return { selected: o.selectedAgentId, follow: o.cameraFollowId };
  });
/** The selected character's label box (drawn at its projected position), or null when it is not on screen. */
const labelBox = async (id: number) => {
  await select(id);
  return p()
    .locator(`[data-testid="agent-overlay"][data-agent-id="${id}"]`)
    .first()
    .boundingBox({ timeout: 2_000 })
    .catch(() => null);
};
const worldPoint = (x: number, y: number) =>
  x > RAIL_PX + 8 &&
  x < 1440 - DI_PANEL_WIDTH_PX &&
  y > 8 &&
  y < 892 &&
  p().evaluate(([xx, yy]) => document.elementFromPoint(xx, yy)?.tagName === 'CANVAS', [x, y] as const);
const stageMission = () =>
  p().evaluate(
    () =>
      document
        .querySelector('[data-testid="di-scan-stage"][data-view="mission"]')
        ?.getAttribute('data-mission-ref') ?? null,
  );
const homeShown = () =>
  p().evaluate(
    () =>
      document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list' &&
      document.querySelector('[data-testid="di-scan-stage"]') === null,
  );
async function backHome(): Promise<void> {
  await p().click('[data-testid="di-scan-back"]');
  await p().waitForFunction(
    () =>
      document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-view') === 'list' &&
      document.querySelector('[data-testid="di-scan-stage"]') === null,
  );
}
/** The Mission whose details opened within `ms` (null: none opened), so a failure is an assertion, never a timeout. */
const openedMission = async (ms = 3_000) => {
  await p()
    .waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]', { timeout: ms })
    .catch(() => undefined);
  return stageMission();
};
/** Make `ref` the selected Home row (its own tab), and return its title as Home shows it. */
async function chooseRow(ref: string): Promise<string> {
  const row = `[data-testid="di-scan-row"][data-mission-ref="${ref}"]`;
  for (const tab of ['open', 'merged', 'closed', 'archived']) {
    await p().click(`[data-testid="di-scan-tab-${tab}"]`);
    if ((await p().locator(row).count()) > 0) break;
  }
  // a click on the row that is ALREADY selected opens it, so only an unselected row is clicked
  if ((await p().getAttribute(row, 'aria-pressed')) !== 'true') await p().click(row);
  assert.equal(await p().getAttribute(row, 'aria-pressed'), 'true', `${ref} is the selected Home row`);
  return (await p().locator(`${row} .di-scan-title`).innerText()).trim();
}
/** A REAL click on character `id` in the world; true once that character itself was hit (a click that hit another is undone). */
async function clickAgent(id: number): Promise<boolean> {
  for (let round = 0; round < 3; round++)
    for (const dy of [6, 12, 18, 24, 30, 36]) {
      const b = await labelBox(id);
      await select(null);
      if (!b) continue;
      const at = { x: b.x + b.width / 2, y: b.y + b.height + dy };
      if (!(await worldPoint(at.x, at.y))) continue;
      await p().mouse.click(at.x, at.y);
      const hit = (await selectedNow()).selected;
      if (hit === id) return true;
      // The click hit ANOTHER agent, or none. A Mission agent's click selects it at once and its stage mounts a moment later, so a DOM
      // read at this instant can miss a stage that is about to appear (measured: 1 click in 39), which would leave it open over the
      // world. The selection says what the click did: a Mission agent's Mission WILL open, so wait for it (it must) and go back.
      if (hit !== null && MISSION_AGENTS.has(hit)) {
        await p()
          .waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]', { timeout: 5_000 })
          .catch(() => {
            throw new Error(`the click selected Mission agent ${hit} but its Mission did not open within 5 s`);
          });
        await backHome();
      } else if ((await stageMission()) !== null) await backHome();
      await select(null);
    }
  return false;
}
/** `ids` with the SEATED characters first: a seated character's label holds still, so a real click on it is stable. */
const seatedFirst = async (ids: number[]) => {
  const seated = await p().evaluate(
    (list) =>
      list.filter(
        (id) => (window as unknown as { __diHarnessOffice: WorldOffice }).__diHarnessOffice.characters.get(id)?.state === 'type',
      ),
    ids,
  );
  return [...seated, ...ids.filter((id) => !seated.includes(id))];
};
/** A real click on the Mission label of one of `ids` (seated first); the id clicked, or null when none was on the world. */
async function clickLabelOf(ids: number[]): Promise<number | null> {
  for (const id of await seatedFirst(ids)) {
    const b = await labelBox(id);
    if (!b || !(await worldPoint(b.x + b.width / 2, b.y + b.height + 12))) continue;
    const label = p().locator(`[data-testid="agent-overlay"][data-agent-id="${id}"] [data-testid="di-mission-label"]`);
    assert.equal(await label.count(), 1, `agent ${id}'s label is a Mission button`);
    assert.match((await label.getAttribute('aria-label')) ?? '', /^Open Mission /);
    if (await label.click({ timeout: 5_000 }).then(() => true, () => false)) return id;
  }
  return null;
}
/** The first of `ids` whose character can be reached by a real click right now. */
async function clickOneOf(ids: number[]): Promise<number | null> {
  for (const id of ids) if (await clickAgent(id)) return id;
  return null;
}

test(
  'AC-4.4-26 CORRECT MISSION: a real click on an agent opens THAT agent\'s Mission, for agents of BOTH drawn Missions, with the other Mission selected on Home',
  async () => {
    await boot();
    for (const [mine, other] of [
      [CORE, HERD],
      [HERD, CORE],
    ] as const) {
      // the title Home shows for THIS Mission, then the OTHER Mission made the selected row
      const mineTitle = await chooseRow(mine);
      await chooseRow(other);
      const clicked = await clickOneOf(OF[mine]!);
      assert.ok(clicked !== null, `a real click reached an agent of ${mine}`);
      assert.equal(await openedMission(), mine, `agent ${clicked} opened its OWN Mission, not the selected ${other}`);
      assert.equal((await p().locator('[data-testid="di-scan-mission-title"]').innerText()).trim(), mineTitle);
      // selection and follow are kept: the click still selects and follows the agent (nothing existing removed)
      assert.deepEqual(await selectedNow(), { selected: clicked, follow: clicked });
      await backHome();
    }
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 CORRECT MISSION: the Mission label of the selected agent is a button that opens THAT Mission, for both drawn Missions',
  async () => {
    await boot();
    for (const [mine, other] of [
      [HERD, CORE],
      [CORE, HERD],
    ] as const) {
      await chooseRow(other);
      const opened = await clickLabelOf(OF[mine]!);
      assert.ok(opened !== null, `a label of ${mine} was on the world`);
      assert.equal(await openedMission(), mine, `label of agent ${opened} opened its OWN Mission, not the selected ${other}`);
      await backHome();
    }
  },
  STEPS_MS,
);

test(
  "AC-4.4-26: a sub-agent opens its PARENT's Mission",
  async () => {
    await boot();
    await chooseRow(HERD);
    let parent: number | null = null;
    for (const id of OF[CORE]!) if ((await labelBox(id)) !== null) parent = parent ?? id;
    await select(null);
    assert.ok(parent !== null, 'a parent of the Core Mission is on screen');
    // the way the app learns of a sub-agent: the server's agentToolStart for the parent's Task tool (boot() has announced
    // the provider's sub-agent tools, as the server does after webviewReady)
    socket!.send(
      JSON.stringify({ type: 'agentToolStart', id: parent, toolId: 'probe-subtask', status: 'Subtask: probe', toolName: 'Task' }),
    );
    const sub = await p()
      .waitForFunction(
        (id) => {
          const o = (window as unknown as { __diHarnessOffice: WorldOffice & { subagentMeta: Map<number, { parentAgentId: number }> } })
            .__diHarnessOffice;
          return [...o.subagentMeta.entries()].find(([, m]) => m.parentAgentId === id)?.[0] ?? false;
        },
        parent,
        { timeout: 10_000 },
      )
      .then((h) => h.jsonValue() as Promise<number>);
    assert.ok(sub < 0, 'a sub-agent was spawned');
    await p().waitForFunction(
      (id) => (window as unknown as { __diHarnessOffice: WorldOffice }).__diHarnessOffice.characters.get(id)?.matrixEffect == null,
      sub,
    );
    assert.ok(await clickAgent(sub), `a real click reached sub-agent ${sub}`);
    assert.equal(await openedMission(), CORE, `sub-agent ${sub} of agent ${parent} opened the parent's Mission`);
  },
  STEPS_MS,
);

test(
  'AC-4.4-26: a runtime agent (its contract never assigns it to a Mission) opens nothing: it is selected and Home stays',
  async () => {
    await boot();
    const clicked = await clickOneOf([9, 10, 11, 12, 13, 14, 15, 16]);
    assert.ok(clicked !== null, 'a real click reached a runtime agent');
    await p().waitForTimeout(500);
    assert.equal(await homeShown(), true, 'no Mission opened');
    assert.equal((await selectedNow()).selected, clicked);
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 Back: a Mission opened from the world returns to Home with the world as it was (same canvas, same projection, same view, selection kept)',
  async () => {
    await boot();
    // seated references: their labels sit at their projected positions, so an unchanged label box means an unchanged projection
    const seated = await p().evaluate(() =>
      [...(window as unknown as { __diHarnessOffice: WorldOffice }).__diHarnessOffice.characters.entries()]
        .filter(([id, ch]) => id > 0 && ch.isReceptionist !== true && ch.state === 'type')
        .map(([id]) => id),
    );
    const refs: number[] = [];
    for (const id of seated) if (refs.length < 2 && (await labelBox(id)) !== null) refs.push(id);
    await select(null);
    assert.ok(refs.length >= 1, `a seated character is on screen (seated: ${seated.join(',')})`);
    const boxes = async () => {
      const out: Array<{ x: number; y: number } | null> = [];
      for (const id of refs) {
        const b = await labelBox(id);
        out.push(b ? { x: Math.round(b.x), y: Math.round(b.y) } : null);
      }
      return out;
    };
    const before = await boxes();
    await p().waitForTimeout(400);
    // CONTROL: the references hold still while nothing happens, so a difference below is the navigation's
    assert.deepEqual(await boxes(), before, 'the seated references are stable');
    const view = () =>
      p().evaluate(() =>
        [...document.querySelectorAll('[data-testid="world-view-controls"] button')].map(
          (b) => `${b.textContent}:${b.getAttribute('aria-pressed')}`,
        ),
      );
    // the world's controls open (item 5), so their state is part of what must come back unchanged
    await openWorldControls();
    const viewBefore = await view();
    assert.ok(viewBefore.length > 0, 'the view controls are drawn');
    await p().evaluate(() => (document.querySelector('canvas')!.dataset.worldProbe = 'kept'));
    const canvasBefore = await box('canvas');
    // open from a Mission LABEL (no camera follow involved), then Back
    const opener = await clickLabelOf([...OF[CORE]!, ...OF[HERD]!]);
    assert.ok(opener !== null, 'a Mission label was on the world');
    assert.notEqual(await openedMission(), null, 'the label opened a Mission');
    await backHome();
    assert.equal(await homeShown(), true, 'Back returned to Home');
    assert.deepEqual(await selectedNow(), { selected: opener, follow: null }, 'the selection is kept');
    assert.equal(
      await p().evaluate(() => document.querySelector('canvas')?.dataset.worldProbe ?? null),
      'kept',
      'the same world canvas (not remounted)',
    );
    assert.deepEqual(await box('canvas'), canvasBefore, 'the world keeps its place and size');
    assert.deepEqual(await view(), viewBefore, 'the world view is unchanged');
    assert.equal(
      await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded'),
      'true',
      'the world controls are still open',
    );
    assert.deepEqual(await boxes(), before, 'the projection (zoom and pan) is unchanged');
  },
  STEPS_MS,
);

// ── pass C item 5: the world's controls behind ONE disclosure (the chevron the locked Home draws at the world's top-right),
// converged to Home tokens and Inter on the scan face; nothing removed; required notices never behind it
const WORLD_CONTROLS = [
  '[data-testid="world-zoom-buttons"]',
  '[data-testid="world-view-controls"]',
  '[data-testid="world-toolbar"]',
  '[data-testid="city-button"]',
  '[data-testid="ambient-legend"]',
  '.di-version-label',
];
const shown = (selector: string) =>
  p().evaluate((s) => {
    const el = document.querySelector<HTMLElement>(s);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }, selector);

test(
  'ITEM 5 DISCLOSURE: the world controls sit behind ONE disclosure at the world\'s top-right, collapsed by default; open shows every one, closed none',
  async () => {
    await boot();
    const toggle = '[data-testid="di-world-disclosure"]';
    assert.equal(await p().locator(toggle).count(), 1, 'the world disclosure is drawn');
    assert.equal(await p().getAttribute(toggle, 'aria-expanded'), 'false', 'collapsed by default');
    const t = await box(toggle);
    const panel = await box('[data-testid="di-office-panel"]');
    assert.ok(t.x > RAIL_PX, 'in the world, right of the rail');
    assert.ok(panel.x - (t.x + t.width) <= 24 && panel.x - (t.x + t.width) >= 0, `at the world's right edge (gap ${panel.x - (t.x + t.width)})`);
    assert.ok(t.y <= 24, `at the world's top (y ${t.y})`);
    for (const s of WORLD_CONTROLS) assert.equal(await shown(s), false, `${s} is behind the disclosure`);
    await p().click(toggle);
    assert.equal(await p().getAttribute(toggle, 'aria-expanded'), 'true');
    for (const s of WORLD_CONTROLS) assert.equal(await shown(s), true, `${s} is shown when open (nothing removed)`);
    await p().click(toggle);
    assert.equal(await p().getAttribute(toggle, 'aria-expanded'), 'false');
    for (const s of WORLD_CONTROLS) assert.equal(await shown(s), false, `${s} is behind the disclosure again`);
  },
  STEPS_MS,
);

test(
  'ITEM 5 REQUIRED NOTICES are never behind it: the FIXTURE label and the connection status show while the controls are collapsed',
  async () => {
    await boot();
    socket!.send(JSON.stringify({ ...LIVE, fixtureMode: true }));
    assert.equal(await p().locator('[data-testid="di-world-disclosure"]').count(), 1, 'the world disclosure is drawn');
    assert.equal(await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded'), 'false');
    const fixtureLabel = async () =>
      (await shown('[data-testid="di-fixture-banner"]')) || (await shown('[data-testid="di-fixture-banner-in-panel"]'));
    await p().waitForFunction(
      () =>
        document.querySelector('[data-testid="di-fixture-banner"], [data-testid="di-fixture-banner-in-panel"]') !== null,
    );
    assert.equal(await fixtureLabel(), true, 'the FIXTURE label shows with the controls collapsed');
    // the connection drops: its status shows with the controls collapsed
    socket!.close();
    await p().waitForSelector('[data-testid="connection-indicator"]', { timeout: 10_000 });
    assert.equal(await shown('[data-testid="connection-indicator"]'), true);
    assert.equal(await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded'), 'false');
  },
  STEPS_MS,
);

test(
  'ITEM 5 KEYBOARD (AC-UX-07): the disclosure is reached by Tab, operated by Enter and Space, and shows a visible focus ring',
  async () => {
    await boot();
    let reached = false;
    for (let i = 0; i < 120 && !reached; i++) {
      await p().keyboard.press('Tab');
      reached = await p().evaluate(
        () => document.activeElement?.getAttribute('data-testid') === 'di-world-disclosure',
      );
    }
    assert.ok(reached, 'Tab reaches the disclosure');
    const ring = await p().evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const s = getComputedStyle(el);
      const probe = document.createElement('div');
      probe.style.color = 'var(--di-focus)';
      document.body.appendChild(probe);
      const focus = getComputedStyle(probe).color;
      probe.remove();
      return { style: s.outlineStyle, width: parseFloat(s.outlineWidth), color: s.outlineColor, focus };
    });
    assert.equal(ring.style, 'solid');
    assert.ok(ring.width >= 2, `ring ${ring.width}px`);
    assert.equal(ring.color, ring.focus, 'drawn with the focus token');
    await p().keyboard.press('Enter');
    assert.equal(await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded'), 'true');
    await p().keyboard.press('Space');
    assert.equal(await p().getAttribute('[data-testid="di-world-disclosure"]', 'aria-expanded'), 'false');
  },
  STEPS_MS,
);

test(
  'ITEM 5 ZOOM SURVIVES COLLAPSED (AC-4.4-26): Ctrl and the wheel over the world still zoom while the controls are behind the disclosure',
  async () => {
    await boot();
    assert.equal(await shown('[data-testid="world-zoom-buttons"]'), false);
    const canvas = await box('canvas');
    await p().mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
    await p().keyboard.down('Control');
    await p().mouse.wheel(0, -240);
    await p().keyboard.up('Control');
    // the world's own zoom level readout (outside the disclosure) reports the change
    const level = await p()
      .waitForFunction(() => {
        const el = [...document.querySelectorAll<HTMLElement>('[data-testid="world-zoom-level"]')][0];
        return el ? el.textContent : false;
      }, undefined, { timeout: 5_000 })
      .then((h) => h.jsonValue() as Promise<string>);
    assert.match(level, /^\d+x$/);
  },
  STEPS_MS,
);

test(
  'ITEM 5 CONVERGED: nothing on the scan face\'s world paints the pixel face, default or open, and every world control is Home chrome (Inter, rounded, no hard pixel shadow)',
  async () => {
    await boot();
    socket!.send(JSON.stringify({ ...LIVE, fixtureMode: true }));
    // an older version seen, so the update notice shows too
    socket!.send(
      JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: '0.0.1', extensionVersion: VERSION }),
    );
    const pixelFaced = () =>
      p().evaluate(() =>
        [...document.querySelectorAll<HTMLElement>('[data-testid="di-world"] *')]
          .filter((el) => {
            if (el.tagName === 'CANVAS' || el.closest('svg')) return false;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return false;
            // only elements that draw text of their own
            const text = [...el.childNodes].some((n) => n.nodeType === 3 && (n.textContent ?? '').trim() !== '');
            return text && /FS Pixel Sans/.test(getComputedStyle(el).fontFamily.split(',')[0] ?? '');
          })
          .map((el) => `${el.tagName}.${el.className}:${(el.textContent ?? '').trim().slice(0, 30)}`),
      );
    assert.deepEqual(await pixelFaced(), [], 'collapsed: no pixel face on the world');
    await openWorldControls();
    // a selected agent's label is world chrome too
    await select(6);
    await p().waitForSelector('[data-testid="agent-overlay"][data-agent-id="6"]');
    assert.deepEqual(await pixelFaced(), [], 'open: no pixel face on the world');
    const chrome = await p().evaluate(() =>
      [
        '[data-testid="di-world-disclosure"]',
        '[data-testid="world-zoom-buttons"] button',
        '[data-testid="world-view-controls"] button',
        '[data-testid="world-toolbar"]',
        '[data-testid="world-toolbar"] button',
        '[data-testid="ambient-legend"]',
        '.di-version-label',
        '.di-version-notice',
        '[data-testid="di-fixture-banner"]',
        '[data-testid="agent-overlay"] .pixel-panel',
      ].flatMap((sel) =>
        [...document.querySelectorAll<HTMLElement>(sel)].map((el) => {
          const s = getComputedStyle(el);
          return { sel, font: s.fontFamily.split(',')[0]!.replace(/"/g, '').trim(), shadow: s.boxShadow, radius: parseFloat(s.borderTopLeftRadius) };
        }),
      ),
    );
    const seen = new Set(chrome.map((c) => c.sel));
    for (const sel of ['[data-testid="di-world-disclosure"]', '[data-testid="world-zoom-buttons"] button', '[data-testid="world-view-controls"] button', '[data-testid="world-toolbar"] button', '[data-testid="ambient-legend"]', '.di-version-label', '.di-version-notice', '[data-testid="agent-overlay"] .pixel-panel'])
      assert.ok(seen.has(sel), `${sel} is drawn`);
    for (const c of chrome) {
      assert.equal(c.font, 'Inter', `${c.sel} is Inter`);
      assert.ok(!/2px 2px 0px/.test(c.shadow), `${c.sel} has no hard pixel shadow (${c.shadow})`);
      if (c.sel !== '.di-version-label') assert.ok(c.radius > 0, `${c.sel} is rounded`);
    }
  },
  STEPS_MS,
);


// ── pass C (B): the scan face's world controls at the narrow end of the desktop matrix, against its OWN measured threshold
const worldPieces = () =>
  p().evaluate(() => {
    const world = document.querySelector('[data-testid="di-world"]')!.getBoundingClientRect();
    const pieces = [
      ['disclosure', '[data-testid="di-world-disclosure"]'],
      ['zoom buttons', '[data-testid="world-zoom-buttons"]'],
      ['view buttons', '[data-testid="world-view-controls"] [data-testid^="world-view-"]'],
      ['district list', '[data-testid="world-district-list"]'],
      ['toolbar', '[data-testid="world-toolbar"]'],
      ['ambient legend', '[data-testid="ambient-legend"]'],
      ['FIXTURE label', '[data-testid="di-fixture-banner"]'],
      ['version label', '.di-version-label'],
      ['update notice', '.di-version-notice'],
    ] as const;
    const out: Array<{ name: string; left: number; right: number; top: number; bottom: number; clipped: boolean }> = [];
    for (const [name, sel] of pieces) {
      const els = [...document.querySelectorAll<HTMLElement>(sel)].filter((e) => e.getBoundingClientRect().width > 0);
      if (els.length === 0) continue;
      const rs = els.map((e) => e.getBoundingClientRect());
      out.push({
        name,
        left: Math.min(...rs.map((r) => r.left)) - world.left,
        right: Math.max(...rs.map((r) => r.right)) - world.left,
        top: Math.min(...rs.map((r) => r.top)) - world.top,
        bottom: Math.max(...rs.map((r) => r.bottom)) - world.top,
        // whole: nothing inside it is cut off sideways (its own content fits its box)
        clipped: els.some((e) => e.scrollWidth > e.clientWidth + 1),
      });
    }
    return { width: world.width, height: world.height, pieces: out };
  });
const overlapping = (pieces: Array<{ name: string; left: number; right: number; top: number; bottom: number }>) => {
  const hits: string[] = [];
  for (let i = 0; i < pieces.length; i++)
    for (let j = i + 1; j < pieces.length; j++) {
      const a = pieces[i]!;
      const b = pieces[j]!;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 0.5 && h > 0.5) hits.push(`${a.name} x ${b.name} (${w.toFixed(1)} x ${h.toFixed(1)})`);
    }
  return hits;
};
const assertWholeAndApart = (
  state: { width: number; height: number; pieces: Array<{ name: string; left: number; right: number; top: number; bottom: number; clipped: boolean }> },
  where: string,
) => {
  console.info(`(B) ${where}: world ${state.width.toFixed(1)} x ${state.height.toFixed(1)}; ${state.pieces.map((x) => `${x.name} ${x.left.toFixed(1)}-${x.right.toFixed(1)}/${x.top.toFixed(1)}-${x.bottom.toFixed(1)}`).join('; ')}`);
  for (const x of state.pieces) {
    assert.ok(x.left >= -0.5 && x.right <= state.width + 0.5, `${where}: ${x.name} is inside the world sideways (${x.left.toFixed(1)} to ${x.right.toFixed(1)} of ${state.width.toFixed(1)})`);
    assert.ok(x.top >= -0.5 && x.bottom <= state.height + 0.5, `${where}: ${x.name} is inside the world vertically`);
    assert.equal(x.clipped, false, `${where}: ${x.name} is whole (nothing in it cut off)`);
  }
  assert.deepEqual(overlapping(state.pieces), [], `${where}: no two world pieces overlap`);
};

test(
  '(B) at 900 x 700, the narrow end of the desktop matrix: the scan face offers its world controls, and every piece is WHOLE, inside the world and NON-OVERLAPPING (controls open, fixture mode, the update notice showing; then the Districts list; then the City panel whole)',
  async () => {
    await boot();
    socket!.send(JSON.stringify({ ...LIVE, fixtureMode: true }));
    socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: '0.0.1', extensionVersion: VERSION }));
    await p().setViewportSize({ width: 900, height: 700 });
    await p().waitForTimeout(600);
    // the world is 900 - panel - rail = 272, at or above the scan face's measured threshold
    const world = await box('[data-testid="di-world"]');
    assert.equal(Math.round(world.width), 900 - DI_PANEL_WIDTH_PX - RAIL_PX);
    assert.ok(world.width >= DI_WORLD_SCAN_CONTROLS_MIN_PX);
    await openWorldControls();
    await p().waitForSelector('.di-version-notice');
    const open = await worldPieces();
    for (const name of ['disclosure', 'zoom buttons', 'view buttons', 'toolbar', 'ambient legend', 'FIXTURE label', 'version label', 'update notice'])
      assert.ok(open.pieces.some((x) => x.name === name), `${name} is drawn at 900`);
    assertWholeAndApart(open, '900 x 700, controls open');
    // THE FLOOR on the required notice (B-E1): it wraps to share its footer, but stays at least 200 px wide at 12 px type, whole
    // in both directions, and topmost (hit-tested with pointer events for the instant of the check, as the capture guard does)
    const notice = await p().evaluate(() => {
      const el = document.querySelector<HTMLElement>('[data-testid="di-fixture-banner"]')!;
      const r = el.getBoundingClientRect();
      const prev = el.style.pointerEvents;
      el.style.pointerEvents = 'auto';
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      el.style.pointerEvents = prev;
      return {
        width: r.width,
        fontPx: parseFloat(getComputedStyle(el).fontSize),
        wholeSideways: el.scrollWidth <= el.clientWidth + 1,
        wholeDown: el.scrollHeight <= el.clientHeight + 1,
        topmost: top !== null && (top === el || el.contains(top)),
      };
    });
    assert.ok(notice.width >= 200, `the FIXTURE label keeps its floor (${notice.width.toFixed(1)} px wide)`);
    assert.equal(notice.fontPx, 12, 'its type is not shrunk');
    assert.equal(notice.wholeSideways, true, 'nothing in it is cut sideways');
    assert.equal(notice.wholeDown, true, 'its wrapped text is not cut below');
    assert.equal(notice.topmost, true, 'nothing covers it');
    // the Districts list opened from its button
    await p().click('[data-testid="world-view-districts"]');
    await p().waitForSelector('[data-testid="world-district-list"]');
    await p().waitForTimeout(200);
    assertWholeAndApart(await worldPieces(), '900 x 700, Districts list open');
    await p().click('[data-testid="world-view-districts"]');
    // the City panel (KEEP interior) opens over the controls' row by design; it must itself be whole and inside the world
    await p().click('[data-testid="city-button"]');
    await p().waitForSelector('#city-controls');
    await p().waitForTimeout(300);
    const city = await p().evaluate(() => {
      const world = document.querySelector('[data-testid="di-world"]')!.getBoundingClientRect();
      const el = document.querySelector<HTMLElement>('#city-controls')!;
      const r = el.getBoundingClientRect();
      return { left: r.left - world.left, right: r.right - world.left, width: world.width, clipped: el.scrollWidth > el.clientWidth + 1 };
    });
    assert.ok(city.left >= -0.5 && city.right <= city.width + 0.5, `the City panel is inside the world (${city.left} to ${city.right} of ${city.width})`);
    assert.equal(city.clipped, false, 'the City panel is whole');
  },
  STEPS_MS,
);

test(
  '(B) BOUNDARY PROOF, OUTSIDE the desktop matrix (a 867 px window; 900 to 1600 never reaches it): one pixel below the scan face\'s threshold the world controls are REMOVED whole (not drawn, out of the tab order), and the FIXTURE label stays exposed (in the panel)',
  async () => {
    await boot();
    socket!.send(JSON.stringify({ ...LIVE, fixtureMode: true }));
    const width = DI_PANEL_WIDTH_PX + RAIL_PX + DI_WORLD_SCAN_CONTROLS_MIN_PX - 1;
    await p().setViewportSize({ width, height: 700 });
    await p().waitForTimeout(600);
    assert.equal(Math.round((await box('[data-testid="di-world"]')).width), DI_WORLD_SCAN_CONTROLS_MIN_PX - 1);
    for (const sel of ['[data-testid="di-world-disclosure"]', '[data-testid="world-zoom-buttons"]', '[data-testid="world-view-controls"]', '[data-testid="world-toolbar"]'])
      assert.equal(await shown(sel), false, `${sel} is not drawn below the threshold`);
    // and nothing of them is reachable by the keyboard
    const reachable = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-di-world-controls] button, [data-di-world-controls] [tabindex]')].filter(
        (el) => !el.closest('[inert]') && el.offsetParent !== null,
      ).length,
    );
    assert.equal(reachable, 0, 'no world control is in the tab order');
    // the required notice: the FIXTURE label is shown, inside the panel, uncovered
    const label = await p().evaluate(() => {
      const el = document.querySelector<HTMLElement>('[data-testid="di-fixture-banner-in-panel"]');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { text: el.textContent, topmost: top !== null && (top === el || el.contains(top)) };
    });
    assert.ok(label, 'the FIXTURE label is drawn in the panel');
    assert.match(label!.text ?? '', /^FIXTURE/);
    assert.equal(label!.topmost, true, 'nothing covers it');
  },
  STEPS_MS,
);


// ── reviewer round 8, blocking 2: a hover-only Mission label must be REACHABLE and CLICKABLE. Every earlier AC-4.4-26 label
// test PRESELECTED the agent (a selected label takes the pointer), which bypassed the hover-only state entirely. Here nothing
// is selected: the pointer hovers the agent, travels to its label in steps, and clicks it.
//
// WHY THE LABEL HAS A GRACE (so nobody reads 400 ms as an arbitrary constant and tunes it): the label is drawn in a layer ABOVE
// the canvas, and the canvas clears the hover the instant the pointer leaves the sprite (and on mouseleave, which entering the
// label causes). The label sits a few pixels above its sprite, so the pointer must cross that gap with no hover; the grace keeps
// the label alive across it. It is TIME, so these tests drive it DETERMINISTICALLY with Playwright's fake clock: after the hover is
// established the clock is PAUSED, and it moves only when the test says (one frame = 16 ms), so nothing here races the machine.
const hoveredNow = () =>
  p().evaluate(
    () => (window as unknown as { __diHarnessOffice: { hoveredAgentId: number | null } }).__diHarnessOffice.hoveredAgentId,
  );
/** Find a point from which a real pointer move HOVERS agent `id` (no click, nothing selected), and move there. */
async function hoverAgent(id: number): Promise<{ x: number; y: number } | null> {
  for (let round = 0; round < 3; round++)
    for (const dy of [6, 12, 18, 24, 30, 36]) {
      const b = await labelBox(id); // selects the agent only to READ where its label is
      await select(null); // ...and clears it: from here nothing is selected
      if (!b) continue;
      const at = { x: b.x + b.width / 2, y: b.y + b.height + dy };
      if (!(await worldPoint(at.x, at.y))) continue;
      await p().mouse.move(at.x, at.y);
      await p().waitForTimeout(120);
      if ((await hoveredNow()) === id) return at;
    }
  return null;
}
/** Stop the page's clock where it is; from here it moves only through frames(). */
const pauseClock = async () => {
  const now = await p().evaluate(() => Date.now());
  await p().clock.pauseAt(now + 50);
};
/** Advance the paused clock by `n` frames (16 ms each), firing the animation frames the overlay renders on. */
const frames = (n: number) => p().clock.runFor(16 * n);
const labelOf = (id: number) =>
  p().locator(`[data-testid="agent-overlay"][data-agent-id="${id}"] [data-testid="di-mission-label"]`);

/** Where agent `id` stands now, in world pixels (read back after frames: the instrument is validated, never assumed). */
const charPos = (id: number) =>
  p().evaluate((i) => {
    const c = (window as unknown as { __diHarnessOffice: { characters: Map<number, { x: number; y: number }> } }).__diHarnessOffice.characters.get(i);
    return c ? { x: Math.round(c.x), y: Math.round(c.y) } : null;
  }, id);

// The layout is SEEDED: Math.random is seeded before the page loads (the same mulberry32 as homeAnchor.test.ts), so the seats are
// the same every run. SEED 4 was chosen from a bounded search (logs/diag-seed-search-start-20261027T021354Z.log, which also showed
// the same seed gives identical positions twice): there, with the HERD agent followed, agent 7 has agent 14 DIRECTLY ABOVE it (the
// recorded failure's geometry: a straight sprite-to-label path hovers 14), and the CORE agents 5, 6 and 8 and the HERD agents 1 to
// 4 have a clean straight path. Both tests READ THE POSITIONS BACK and FAIL (they do not skip) if the seed stops giving this.
const SEED = 4;
// A real pointer reports a move every few pixels, and the gap between a sprite and its label is only a few pixels: the approach is
// walked in steps of this size so that at least one step lands IN the gap (a larger step could jump from the sprite into the label and
// never exercise the hold or the grace at all).
const APPROACH_STEP_PX = 2;
const CLEAN_SUBJECT = { [CORE]: 6, [HERD]: 2 } as Record<string, number>;

/** Boot the seeded layout with the other Mission followed on Home (as a person would have it), and let the camera settle. */
async function bootSeeded(other: string): Promise<void> {
  await boot({ fakeClock: true, seed: SEED });
  await chooseRow(other);
  await p().waitForTimeout(1500); // the camera settles on the followed agent (the clock still runs here)
}
/** Move the real pointer along `points` in steps of at most `stepPx`, ONE frame of page clock per step, calling `each` after every step. */
async function movePointer(points: Array<{ x: number; y: number }>, stepPx: number, each: (k: number, at: { x: number; y: number }) => Promise<void>): Promise<void> {
  let k = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    const n = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / stepPx));
    for (let j = 1; j <= n; j++) {
      const at = { x: a.x + ((b.x - a.x) * j) / n, y: a.y + ((b.y - a.y) * j) / n };
      await p().mouse.move(at.x, at.y);
      await frames(1);
      await each(++k, at);
    }
  }
}
const underPointerIsLabel = (x: number, y: number) =>
  p().evaluate(([xx, yy]) => { const el = document.elementFromPoint(xx as number, yy as number); return el !== null && el.closest('[data-testid="di-mission-label"]') !== null; }, [x, y] as const);

test(
  'AC-4.4-26 HOVER TO LABEL, NO PRESELECTION (seeded layout): hover an UNSELECTED agent, its Mission label appears, the pointer travels onto it with the label present at every step, and clicking it opens THAT Mission (both drawn Missions, the other one selected on Home; the page clock is driven, not waited)',
  async () => {
    for (const [mine, other] of [
      [CORE, HERD],
      [HERD, CORE],
    ] as const) {
      const id = CLEAN_SUBJECT[mine]!;
      await bootSeeded(other);
      const at = await hoverAgent(id);
      assert.ok(at, `seed ${SEED}: agent ${id} (of ${mine}) can be hovered`);
      await pauseClock();
      await frames(2);
      // the hover-only state: this agent is HOVERED and NOT selected, and its label is drawn
      assert.notEqual((await selectedNow()).selected, id, 'the agent is not selected (no preselection)');
      assert.equal(await labelOf(id).count(), 1, `agent ${id}: hovering draws its Mission label`);
      const box = await labelOf(id).boundingBox();
      assert.ok(box, 'the label has a box');
      const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
      assert.ok(to.x > RAIL_PX && to.x < 1440 - DI_PANEL_WIDTH_PX && to.y > 8, `agent ${id}: the label's centre ${JSON.stringify(to)} is on the screen`);
      // the straight approach, a real pointer, ONE frame of clock per step: the hover must never move to another agent (the seeded
      // geometry has a clean path: a crossing here means the seed stopped giving it, and FAILS), and the label is there at every step
      await movePointer([at, to], APPROACH_STEP_PX, async (k) => {
        assert.ok([null, id].includes(await hoveredNow()), `agent ${id}: the pointer is over another agent at step ${k}: the seeded geometry is not the expected one`);
        assert.equal(await labelOf(id).count(), 1, `agent ${id}: the label is still there at step ${k} of the approach`);
      });
      assert.equal(await underPointerIsLabel(to.x, to.y), true, `agent ${id}: the Open Mission button is what is under the pointer`);
      assert.notEqual((await selectedNow()).selected, id, 'still not selected');
      // while the pointer is ON the label it is HELD, well past the grace (a second of page time)
      await frames(63);
      assert.equal(await labelOf(id).count(), 1, `agent ${id}: the label stays while the pointer is on it`);
      await p().mouse.click(to.x, to.y);
      await frames(8);
      assert.equal(await openedMission(), mine, `clicking agent ${id}'s hovered label opened its OWN Mission, not the selected ${other}`);
    }
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 HOVER TO LABEL WITH A NEIGHBOUR DIRECTLY ABOVE (seeded: agent 7 with agent 14 above it, the recorded failure): the straight approach hovers the neighbour and the label correctly goes (CONTROL), a real approach AROUND the neighbour keeps the label, and clicking it opens agent 7\'s Mission',
  async () => {
    await bootSeeded(HERD);
    // PRECONDITION, read back: agent 14 stands directly above agent 7 (same column, one seat up); FAIL if the seed stopped giving it
    const p7 = await charPos(7);
    const p14 = await charPos(14);
    assert.ok(p7 && p14, 'agents 7 and 14 exist');
    assert.equal(p14.x, p7.x, `seed ${SEED}: agent 14 is in agent 7's column (${JSON.stringify(p14)} vs ${JSON.stringify(p7)})`);
    assert.ok(p7.y - p14.y > 0 && p7.y - p14.y <= 24, `seed ${SEED}: agent 14 is one seat above agent 7 (dy ${p7.y - p14.y})`);
    const at = await hoverAgent(7);
    assert.ok(at, 'agent 7 can be hovered');
    await pauseClock();
    await frames(2);
    assert.notEqual((await selectedNow()).selected, 7, 'agent 7 is not selected (no preselection)');
    const box = await labelOf(7).boundingBox();
    assert.ok(box, "agent 7's label is drawn while it is hovered");
    const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };

    // CONTROL (a real pointer): the STRAIGHT approach crosses agent 14's sprite: the hover SWITCHES to 14 and agent 7's label goes.
    // That is correct hover-switch behaviour, and it shows this geometry IS the recorded failure's.
    let switchedTo: number | null = null;
    await movePointer([at, to], 4, async () => {
      const h = await hoveredNow();
      if (switchedTo === null && h !== null && h !== 7) switchedTo = h;
    });
    assert.equal(switchedTo, 14, 'CONTROL: the straight approach hovers agent 14 (the neighbour above), as in the recorded failure');
    assert.equal(await labelOf(7).count(), 0, "CONTROL: agent 7's label goes when the hover switches to agent 14 (correct hover-switch behaviour)");

    // THE APPROACH AROUND: back on agent 7, then beside the neighbour's column (its sampled hover extent plus a margin), up to the
    // label's lower edge and in; the pointer is REAL, one frame per step, and the hover must stay on nobody else throughout
    await p().mouse.move(at.x, at.y);
    await frames(2);
    assert.equal(await hoveredNow(), 7, 'back on agent 7');
    assert.equal(await labelOf(7).count(), 1, "agent 7's label is back");
    // where the neighbour's column ends: scan a row through the neighbour's band, right of the sprite column (real moves, reading the hover)
    let edge = at.x;
    for (let x = at.x; x <= at.x + 60; x += 3) {
      await p().mouse.move(x, box.y + box.height + 6);
      const h = await hoveredNow();
      if (h === 14 || h === 7) edge = x;
    }
    const detourX = edge + 14;
    const inside = { x: detourX, y: box.y + box.height - 8 };
    await p().mouse.move(at.x, at.y);
    await frames(1);
    await movePointer([at, { x: detourX, y: at.y }, inside, to], APPROACH_STEP_PX, async (k) => {
      assert.ok([null, 7].includes(await hoveredNow()), `the pointer is over agent ${await hoveredNow()} at step ${k} of the approach around: the detour is not clear`);
      assert.equal(await labelOf(7).count(), 1, `agent 7's label is still there at step ${k} of the approach around`);
    });
    assert.equal(await underPointerIsLabel(to.x, to.y), true, "the Open Mission button is under the pointer");
    assert.notEqual((await selectedNow()).selected, 7, 'still not selected');
    await p().mouse.click(to.x, to.y);
    await frames(8);
    assert.equal(await openedMission(), CORE, "clicking agent 7's label opened agent 7's Mission (CORE), not the selected HERD row");
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 HOVER LABEL IS NEVER STALE (seeded): hovering agent B removes agent A\'s label at once, and after the pointer leaves for empty world the label survives the grace window and is then gone',
  async () => {
    await bootSeeded(HERD);
    // two agents at different places, named by the seeded layout (CORE agents 5 and 6), each verified hoverable
    const a = { id: 5, pt: await hoverAgent(5) };
    const b = { id: 6, pt: await hoverAgent(6) };
    assert.ok(a.pt && b.pt, 'agents 5 and 6 can be hovered');
    assert.ok(Math.abs(a.pt.x - b.pt.x) > 40 || Math.abs(a.pt.y - b.pt.y) > 40, 'at different places');
    // SWITCH: hover A (its label is drawn), then B: A's label must be gone AT ONCE, B's drawn (the grace must not keep A's up).
    // A REGRESSION GUARD, NOT SHOWN RED, and it cannot be shown red against this implementation: the grace follows the MOST RECENT
    // hovered agent (and does not apply while any agent is hovered), so a stale PREVIOUS-agent label is unreachable by
    // construction (a mutant that ignores the hover test makes the grace hold B, not A; tried once, logged, not pursued).
    await p().mouse.move(a.pt.x, a.pt.y);
    assert.equal(await hoveredNow(), a.id, 'A is hovered');
    await pauseClock();
    await frames(2);
    assert.equal(await labelOf(a.id).count(), 1, 'A shows its label');
    await p().mouse.move(b.pt.x, b.pt.y);
    await frames(2);
    assert.equal(await hoveredNow(), b.id, 'B is hovered');
    assert.equal(await labelOf(a.id).count(), 0, "A's label is gone the moment B is hovered (the grace does not hold it)");
    assert.equal(await labelOf(b.id).count(), 1, "B's label is drawn");
    // EXPIRY: the pointer leaves B for empty world (one real move; then ONLY the clock moves)
    const away = { x: RAIL_PX + 30, y: 860 };
    assert.ok(await worldPoint(away.x, away.y), 'the away point is on the world');
    await p().mouse.move(away.x, away.y);
    await frames(1);
    await frames(18); // about 300 ms of page time after the hover cleared: inside the grace
    assert.equal(await labelOf(b.id).count(), 1, `agent ${b.id}: inside the grace window the label is still drawn`);
    await frames(50); // about 1.1 s: well after it
    assert.equal(await labelOf(b.id).count(), 0, `agent ${b.id}: after the grace window the label is gone (nothing sticks)`);
    assert.equal(await labelOf(a.id).count(), 0, `agent ${a.id}: and A's label is still gone`);
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 HOVER TO LABEL FOR A DONE AGENT (seeded): an agent whose turn is finished (the green checkmark) still gets a reachable Mission label while the pointer travels from its sprite to the label, clicking it opens that Mission, and when nobody is reaching for it the label is gone and the checkmark is what remains',
  async () => {
    await bootSeeded(HERD);
    const id = CLEAN_SUBJECT[CORE]!;
    const at = await hoverAgent(id);
    assert.ok(at, `agent ${id} can be hovered`);
    await pauseClock();
    await frames(2);
    // the Done state by the product's OWN call (what the 'agentStatus: waiting' message does), then READ BACK: a waiting bubble that is
    // not awaiting input is what the overlay treats as Done
    await p().evaluate((i) => (window as unknown as { __diHarnessOffice: { showWaitingBubble(i: number, awaiting: boolean): void } }).__diHarnessOffice.showWaitingBubble(i, false), id);
    await frames(2);
    const bubble = await p().evaluate(
      (i) => {
        const c = (window as unknown as { __diHarnessOffice: { characters: Map<number, { bubbleType: string | null; waitingAwaitingInput?: boolean }> } }).__diHarnessOffice.characters.get(i)!;
        return { bubbleType: c.bubbleType, waitingAwaitingInput: c.waitingAwaitingInput ?? false };
      },
      id,
    );
    assert.deepEqual(bubble, { bubbleType: 'waiting', waitingAwaitingInput: false }, `agent ${id} is in the Done state`);
    assert.equal(await hoveredNow(), id, 'still hovered');
    assert.notEqual((await selectedNow()).selected, id, 'not selected (no preselection)');
    assert.equal(await labelOf(id).count(), 1, `a HOVERED Done agent shows its label (the precondition: the label exists to be reached)`);
    const box = await labelOf(id).boundingBox();
    assert.ok(box, 'the label has a box');
    const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    // the approach: the label must be there at EVERY step, including the steps in the gap where nothing is hovered
    let hoverFreeSteps = 0;
    await movePointer([at, to], APPROACH_STEP_PX, async (k) => {
      if ((await hoveredNow()) === null) hoverFreeSteps++;
      assert.equal(await labelOf(id).count(), 1, `Done agent ${id}: the label is still there at step ${k} of the approach (${hoverFreeSteps} steps so far with nothing hovered)`);
    });
    assert.ok(hoverFreeSteps > 0, 'PRECONDITION OF THE PROOF: some steps had nothing hovered (the gap was actually crossed)');
    assert.equal(await underPointerIsLabel(to.x, to.y), true, 'the Open Mission button is under the pointer');
    await frames(63);
    assert.equal(await labelOf(id).count(), 1, 'the label stays while the pointer is on it');
    await p().mouse.click(to.x, to.y);
    await frames(8);
    assert.equal(await openedMission(), CORE, `clicking the Done agent's label opened its Mission`);
  },
  STEPS_MS,
);

test(
  'AC-4.4-26 A DONE AGENT NOBODY IS REACHING FOR resolves to its checkmark: with the pointer elsewhere there is no label panel for it (it would cover the checkmark), the grace gives it nothing, and the Done state is still there',
  async () => {
    await bootSeeded(HERD);
    const id = CLEAN_SUBJECT[CORE]!;
    const at = await hoverAgent(id);
    assert.ok(at, `agent ${id} can be hovered`);
    await pauseClock();
    await frames(2);
    await p().evaluate((i) => (window as unknown as { __diHarnessOffice: { showWaitingBubble(i: number, awaiting: boolean): void } }).__diHarnessOffice.showWaitingBubble(i, false), id);
    await frames(2);
    assert.equal(await labelOf(id).count(), 1, 'hovered and Done: the label shows (the contrast case for the assertions below)');
    // the pointer leaves for empty world; later, well after any grace, no label panel for the Done agent
    const away = { x: RAIL_PX + 30, y: 860 };
    assert.ok(await worldPoint(away.x, away.y), 'the away point is on the world');
    await p().mouse.move(away.x, away.y);
    await frames(1);
    await frames(80); // about 1.3 s of page time
    assert.equal(await hoveredNow(), null, 'nothing is hovered');
    assert.equal(await labelOf(id).count(), 0, `nobody is reaching for Done agent ${id}: no label panel (the checkmark is what remains)`);
    const bubble = await p().evaluate(
      (i) => (window as unknown as { __diHarnessOffice: { characters: Map<number, { bubbleType: string | null }> } }).__diHarnessOffice.characters.get(i)!.bubbleType,
      id,
    );
    assert.equal(bubble, 'waiting', 'the Done state (the checkmark) is still there: the label fix removed nothing');
  },
  STEPS_MS,
);
