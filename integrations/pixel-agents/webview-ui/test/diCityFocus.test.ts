/**
 * The CITY POPOVER and keyboard focus, through the ACTUAL App, in a real browser, over the SHIPPED living metropolis with a labelled FIXTURE office
 * state. Nothing here is live Mission data; this is integration over source (the App from Vite's dev server), not the production package.
 *
 * REQUIREMENT: while the City popover is open it covers part of the world, and keyboard focus must not land INVISIBLY behind it. The popover is
 * non-modal, so the toolbar and the office panel stay reachable (they are drawn and focus is visible there); the world's other controls (zoom, the
 * view buttons, the reception target, the update notice) are the ones it hides, and they are removed from the tab order while it is open (inert, so
 * not merely covered: clipping, covering and z-index do not remove a control from the tab order). Escape and Close return focus to a named control
 * that is drawn, never the page body, and the City choices and their state are untouched.
 *
 * Every sequence is walked with the REAL keyboard, FORWARD (Tab) and BACKWARD (Shift+Tab), from the previously failing contexts: 320 x 568 after the
 * office is minimized, and two normal widths. Backward is asserted as strictly as forward: containment leaks backwards first.
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
import { tabProblems, tabTraversal } from './diTabTraversal.ts';

const STEPS_MS = 300_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LIVE = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { states: Record<'live', DiOfficeState> }
).states.live;
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
let connected: Promise<WebSocket> = Promise.resolve(null as unknown as WebSocket);
let resolveConnected: (ws: WebSocket) => void = () => undefined;

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    if (msg.type === 'webviewReady') {
      socket = ws;
      resolveConnected(ws);
    }
  });
}

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
    wss!.handleUpgrade(req, sock, head, (ws) => serve(ws));
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app.html`;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (world yield): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const GATE = '[data-di-world-controls]';
/** A narrow world pins the version label in the rail footer, outside the controls gate. It is still world furniture. */
const WORLD_REGION = `${GATE}, [data-testid="di-world-rail-footer"] .di-version-label`;

/** Open the App at a window size, hand it the shipped world and the fixture's agents, and wait until it is drawn. */
async function boot(
  width: number,
  height: number,
  opts: { fixture?: boolean; scale?: number } = {},
): Promise<void> {
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({
    viewport: { width, height },
    deviceScaleFactor: opts.scale ?? 1,
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  // an EXPLICIT fixture preview (the floating fixture banner exists only then; it once painted over the City popover's controls)
  socket!.send(JSON.stringify(opts.fixture ? { ...LIVE, fixtureMode: true } : LIVE));
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
  await page.waitForFunction(
    () => {
      const office = (
        window as unknown as {
          __diHarnessOffice?: { characters: Map<number, unknown>; layout: { cols: number } };
        }
      ).__diHarnessOffice;
      return office !== undefined && office.characters.size >= 4 && office.layout.cols === 352;
    },
    undefined,
    { timeout: 60_000 },
  );
  await p().waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
}

/** Show the update notice (it auto-closes after 20 s, so this is sent right before it is needed). */
async function showUpdateNotice(): Promise<void> {
  socket!.send(
    JSON.stringify({
      type: 'settingsLoaded',
      soundEnabled: false,
      showAreas: false,
      extensionVersion: '9.9.0',
      lastSeenVersion: '1.0',
    }),
  );
  await p().waitForTimeout(400);
}
const minimizeOffice = async () => {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().waitForTimeout(400);
};
const CITY = '#city-controls';
const REGIONS = {
  city: CITY,
  world: WORLD_REGION,
  panel: '[data-testid="di-office-panel"]',
  strip: '[data-testid="di-minimized-tray"]',
};
const BAR = { city: 1, world: 1, panel: 1, strip: 1, default: 1 };
/** The toolbar is the only world furniture that stays in the tab order while the popover is open (it is drawn, uncovered, and holds the City button). */
const TOOLBAR_ONLY = ['Layout', 'city-button', 'Settings'];
/**
 * The Version Indicator's controls, as the traversal NAMES them (its id is data-testid, else aria-label, else the first words of the text: a traversal identifier, not the accessible name),
 * for the version `showUpdateNotice` sends (9.9.0, shown as 9.9): the update card's face, its dismiss, and the version label. The card and the label were onClick-only divs and the dismiss
 * was a bare 'x'; they are real buttons now, so they ARE tab stops, and they sit inside the same suspended gate as the rest of the background controls. They are listed here so the
 * invariant stays EXACT: present before the popover, absent while it covers them, present again after. A control added to the Version Indicator later must be added here (the enumeration from
 * the page, below, fails until it is) and not left reachable under the popover.
 */
const NOTICE_STOPS = [
  "Updated to v9.9!See what's new",
  'version-notice-dismiss',
  "v9.9, see what's new",
];
const SUSPENDED = [
  'world-view-office',
  'world-view-deck',
  'world-view-woods',
  'world-view-office-atrium',
  'world-view-office-observatory',
  'world-view-map',
  'world-view-districts',
  ...NOTICE_STOPS,
];

/** What has focus: its test id, whether it is the page body, and whether it is really drawn on top where it sits (a hit test, pointer-events neutralized). */
const focusState = () =>
  p().evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    const body = el === null || el === document.body || el === document.documentElement;
    if (body || !el) return { body: true, id: null as string | null, drawn: false };
    const probe = document.createElement('style');
    probe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
    document.head.append(probe);
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(
      Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2)),
      Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2)),
    );
    probe.remove();
    const inside =
      r.left >= -0.5 &&
      r.right <= innerWidth + 0.5 &&
      r.top >= -0.5 &&
      r.bottom <= innerHeight + 0.5;
    return {
      body: false,
      id:
        el.getAttribute('data-testid') ?? el.getAttribute('aria-label') ?? el.tagName.toLowerCase(),
      drawn: inside && top !== null && el.contains(top),
    };
  });
const pressedState = (id: string) =>
  p().$eval(`[data-testid="${id}"]`, (el) => el.getAttribute('aria-pressed'));
const openCityByKeyboard = async () => {
  await p().focus('[data-testid="city-button"]');
  await p().keyboard.press('Enter');
  await p().waitForSelector(CITY);
  await p().waitForTimeout(300);
};
const walk = (backward: boolean) => tabTraversal(p(), { max: 400, regions: REGIONS, backward });

/**
 * `notice`: the update notice (and its dismiss control, which the popover also covers) auto-closes 20 s after it appears, and a walk over the office
 * panel's ~170 stops takes longer than that, so the notice is only asserted where the walks are short (the office minimized). Where it is not asserted it
 * is not shown, so no assertion about it can pass vacuously.
 */
const CONTEXTS = [
  {
    label: '320 x 568 after Minimize (the previously failing narrow context), fixture preview',
    width: 320,
    height: 568,
    scale: 1,
    minimized: true,
    notice: true,
    fixture: true,
  },
  {
    label: '1440 x 900 with the office minimized (normal width, notice up), fixture preview',
    width: 1440,
    height: 900,
    scale: 1,
    minimized: true,
    notice: true,
    fixture: true,
  },
  {
    label: '900 x 700 with the office minimized (notice up), fixture preview',
    width: 900,
    height: 700,
    scale: 1,
    minimized: true,
    notice: true,
    fixture: true,
  },
  {
    label:
      '720 x 450 at 2x (ZOOM-EQUIVALENT, not a native Command-plus test) with the office minimized (notice up), fixture preview',
    width: 720,
    height: 450,
    scale: 2,
    minimized: true,
    notice: true,
    fixture: true,
  },
  {
    label: '900 x 700 with the office open, fixture preview',
    width: 900,
    height: 700,
    scale: 1,
    minimized: false,
    notice: false,
    fixture: true,
  },
  {
    label: '1440 x 900 with the office open (normal width), fixture preview',
    width: 1440,
    height: 900,
    scale: 1,
    minimized: false,
    notice: false,
    fixture: true,
  },
];

for (const c of CONTEXTS) {
  test(
    `CITY POPOVER at ${c.label}: no invisible focus behind it going forward OR backward, Escape and Close return focus to a drawn named control, the choices survive, and the background controls return when it closes`,
    async () => {
      await boot(c.width, c.height, { fixture: c.fixture, scale: c.scale });
      if (c.minimized) await minimizeOffice();
      if (c.notice) await showUpdateNotice();

      // 0. the baseline with the popover CLOSED: the controls the popover will hide are in the sequence, so their later absence is the popover's doing
      const before = await walk(false);
      const beforeWorld = before.stops.filter((s) => s.region === 'world').map((s) => s.id);
      for (const id of [
        'world-view-map',
        'world-view-districts',
        ...(c.notice ? NOTICE_STOPS : []),
      ])
        assert.ok(
          beforeWorld.includes(id),
          `${c.label}: with the popover closed ${id} is a tab stop (so its absence below is caused by the popover)`,
        );

      // 0b. THE LIST IS COMPLETE: enumerated from the PAGE (not from the traversal), the tabbable controls inside the gate that holds the Version Indicator are exactly as many as are listed,
      // so a control added to it cannot escape the invariant by simply not being named here
      if (c.notice) {
        const tabbable = await p().evaluate(() => {
          const selector =
            'button:not([disabled]):not([tabindex="-1"]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
          const label = document.querySelector('.di-version-label');
          const gate = label?.closest('[data-di-world-controls]');
          if (gate) return gate.querySelectorAll(selector).length;
          const noticeGate = document
            .querySelector('.di-version-notice')
            ?.closest('[data-di-world-controls]');
          const inNotice = noticeGate ? noticeGate.querySelectorAll(selector).length : 0;
          return inNotice + (label ? 1 : 0);
        });
        assert.equal(
          tabbable,
          NOTICE_STOPS.length,
          `${c.label}: the Version Indicator's gate holds ${tabbable} tabbable controls and ${NOTICE_STOPS.length} are listed as suspended with the popover`,
        );
      }

      // 1. open it by KEYBOARD and walk the whole sequence FORWARD and BACKWARD
      await openCityByKeyboard();
      const forward = await walk(false);
      const backward = await walk(true);
      for (const [name, t] of [
        ['forward (Tab)', forward],
        ['backward (Shift+Tab)', backward],
      ] as const) {
        assert.notEqual(t.ended, 'max', `${c.label}, ${name}: the walk reached an end`);
        const problems = tabProblems(t, BAR).map((x) => `${x.id}: ${x.why.join(' / ')}`);
        assert.deepEqual(
          problems,
          [],
          `${c.label}, ${name}: every stop is fully drawn with a visible focus indicator, so focus never lands behind the popover`,
        );
        const worldIds = t.stops.filter((s) => s.region === 'world').map((s) => s.id);
        assert.deepEqual(
          worldIds.sort(),
          [...TOOLBAR_ONLY].sort(),
          `${c.label}, ${name}: the world furniture still reachable is the toolbar alone (saw ${JSON.stringify(worldIds)})`,
        );
        for (const id of c.notice ? SUSPENDED : SUSPENDED.filter((x) => !NOTICE_STOPS.includes(x)))
          assert.ok(
            !t.stops.some((s) => s.id === id),
            `${c.label}, ${name}: ${id} is not in the tab sequence while the popover covers it`,
          );
        assert.ok(
          t.stops.filter((s) => s.region === 'city').length >= 15,
          `${c.label}, ${name}: the popover's own controls are in the sequence`,
        );
      }
      assert.deepEqual(
        backward.stops.map((s) => s.id),
        [...forward.stops.map((s) => s.id)].reverse(),
        `${c.label}: Shift+Tab visits exactly the Tab stops in the opposite order`,
      );

      // 2. the first and last popover controls: one step either way lands on a drawn named control, never on a hidden one and never on the body
      const firstCity = forward.stops.findIndex((s) => s.region === 'city');
      const lastCity =
        forward.stops.length -
        1 -
        [...forward.stops].reverse().findIndex((s) => s.region === 'city');
      assert.ok(
        firstCity > 0 &&
          forward.stops[firstCity - 1].hitSamples === forward.stops[firstCity - 1].sampled,
        `${c.label}: the stop before the popover (${forward.stops[firstCity - 1]?.id}) is drawn`,
      );
      const afterId = forward.stops[lastCity + 1]?.id ?? null;
      assert.ok(
        afterId === null ||
          forward.stops[lastCity + 1].hitSamples === forward.stops[lastCity + 1].sampled,
        `${c.label}: the stop after the popover (${afterId}) is drawn`,
      );

      // 3. ESCAPE from inside the popover: it closes and focus lands on the City button, drawn, not the body
      await p().focus('[data-testid="city-lighting-night"]');
      await p().keyboard.press('Escape');
      await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });
      let f = await focusState();
      assert.deepEqual(
        { body: f.body, id: f.id, drawn: f.drawn },
        { body: false, id: 'city-button', drawn: true },
        `${c.label}: after Escape from inside, focus is on the City button and it is drawn`,
      );

      // 4. CLOSE by keyboard
      await openCityByKeyboard();
      await p().focus('[data-testid="city-controls-close"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });
      f = await focusState();
      assert.deepEqual(
        { body: f.body, id: f.id, drawn: f.drawn },
        { body: false, id: 'city-button', drawn: true },
        `${c.label}: after Close by keyboard, focus is on the City button and it is drawn`,
      );

      // 5. ESCAPE pressed on the City button itself (opened by keyboard): closes, focus stays on it
      await openCityByKeyboard();
      await p().focus('[data-testid="city-button"]');
      await p().keyboard.press('Escape');
      await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });
      f = await focusState();
      assert.deepEqual(
        { body: f.body, id: f.id, drawn: f.drawn },
        { body: false, id: 'city-button', drawn: true },
        `${c.label}: after Escape on the City button, focus is still there and drawn`,
      );

      // 6. the CHOICES and their saved state survive: choose with the keyboard, close, reopen
      await openCityByKeyboard();
      const nightBefore = await pressedState('city-lighting-night');
      await p().focus('[data-testid="city-lighting-night"]');
      await p().keyboard.press('Enter');
      await p().waitForTimeout(300);
      const nightChosen = await pressedState('city-lighting-night');
      assert.equal(
        nightChosen,
        'true',
        `${c.label}: choosing Night by keyboard sets it (was ${nightBefore})`,
      );
      await p().keyboard.press('Escape');
      await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });
      await openCityByKeyboard();
      assert.equal(
        await pressedState('city-lighting-night'),
        'true',
        `${c.label}: the choice is still set after closing and reopening`,
      );
      await p().keyboard.press('Escape');
      await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });

      // 7. the background controls are BACK in the sequence, fully drawn (suspension is lifted when the popover closes)
      const after = await walk(false);
      const afterWorld = after.stops.filter((s) => s.region === 'world').map((s) => s.id);
      for (const id of [
        'world-view-map',
        'world-view-districts',
        ...(c.notice ? NOTICE_STOPS : []),
      ])
        assert.ok(
          afterWorld.includes(id),
          `${c.label}: ${id} is a tab stop again once the popover has closed`,
        );
      assert.deepEqual(
        tabProblems(after, BAR).map((x) => `${x.id}: ${x.why.join(' / ')}`),
        [],
        `${c.label}: after closing, every stop is fully drawn`,
      );
      assert.equal(
        await p().evaluate(() => document.querySelectorAll('.di-world-controls[inert]').length),
        0,
        `${c.label}: nothing is left inert`,
      );
    },
    STEPS_MS * 2,
  );
}

/*
 * THE MAP AND THE CITY POPOVER TOGETHER. An open Map owns the keyboard through a WINDOW-level capture listener (mapKeyGuard.ts) that swallows every key
 * and turns Escape into "close the Map". `inert` affects elements, never a window listener, and the City popover is not a dialog, so it does not make #root
 * inert: before this was fixed, Escape pressed inside the City popover closed the MAP (destroying its open state) and left the City open. The foreground
 * surface must receive Escape, and the Map must keep its open state and get the keyboard back when the City closes. A GLOBAL resource (a window
 * listener) must not be coordinated by a LOCAL condition: the question it answers is "is something foreground over me".
 */
const MAP_SEL = '[data-testid="world-map"]';
const mapDrawn = () => p().locator(MAP_SEL).count();
const cityOpen = () => p().locator(CITY).count();
/** Do the page's own keydown handlers still receive a key? An open Map's guard swallows every key, so this is false while it owns the keyboard. */
async function handlersReceiveKeys(): Promise<boolean> {
  await p().evaluate(() => {
    if (document.documentElement.dataset.keysSeen === undefined) {
      document.documentElement.dataset.keysSeen = '0';
      window.addEventListener('keydown', () => {
        document.documentElement.dataset.keysSeen = String(
          Number(document.documentElement.dataset.keysSeen) + 1,
        );
      });
    }
  });
  const read = () => p().evaluate(() => Number(document.documentElement.dataset.keysSeen));
  const before = await read();
  await p().keyboard.press('ArrowDown');
  return (await read()) > before;
}

test(
  'THE MAP THEN THE CITY POPOVER (real sequence): Escape inside the popover closes the POPOVER, the Map keeps its open state and gets the keyboard back, the next Escape closes the Map; the other order is not reachable',
  async () => {
    await boot(1440, 900);
    // the Map is open, then the City popover is opened BY KEYBOARD from its button (the City button is the topmost thing at its centre, so a pointer reaches it too)
    await p().click('[data-testid="world-view-map"]');
    await p().waitForSelector(MAP_SEL);
    const mapBox = await p().locator(MAP_SEL).boundingBox();
    assert.equal(
      await handlersReceiveKeys(),
      false,
      'teeth: with the Map open its guard owns the keyboard',
    );
    await openCityByKeyboard();
    assert.deepEqual(
      { map: await mapDrawn(), city: await cityOpen() },
      { map: 1, city: 1 },
      'both are open',
    );

    // while the popover is foreground the Map's guard yields (the keys reach the popover)
    assert.equal(
      await handlersReceiveKeys(),
      true,
      'with the City popover foreground over the Map, the Map no longer swallows the keyboard',
    );

    // ESCAPE inside the popover: the popover closes, the MAP IS STILL OPEN with the same geometry, focus lands on the City button, drawn
    await p().focus('[data-testid="city-lighting-night"]');
    await p().keyboard.press('Escape');
    await p().waitForSelector(CITY, { state: 'detached', timeout: 10_000 });
    assert.equal(
      await mapDrawn(),
      1,
      'Escape closed the foreground popover and NOT the Map: its open state is preserved',
    );
    assert.deepEqual(
      await p().locator(MAP_SEL).boundingBox(),
      mapBox,
      'and the Map is exactly where it was',
    );
    const f = await focusState();
    assert.deepEqual(
      { body: f.body, id: f.id, drawn: f.drawn },
      { body: false, id: 'city-button', drawn: true },
      'focus returned to the drawn City button',
    );

    // the Map owns the keyboard again, and the NEXT Escape closes it
    assert.equal(
      await handlersReceiveKeys(),
      false,
      'once the popover has closed the Map owns the keyboard again',
    );
    await p().keyboard.press('Escape');
    await p().waitForSelector(MAP_SEL, { state: 'detached', timeout: 10_000 });

    // THE OTHER ORDER: with the popover open first, the Map control is inert: it cannot take focus and a click does not open the Map
    await openCityByKeyboard();
    const mapControl = await p().locator('[data-testid="world-view-map"]').boundingBox();
    if (mapControl)
      await p().mouse.click(
        mapControl.x + mapControl.width / 2,
        mapControl.y + mapControl.height / 2,
      );
    await p().waitForTimeout(300);
    assert.equal(
      await mapDrawn(),
      0,
      'a click on the Map control under the open popover does not open the Map',
    );
    await p()
      .focus('[data-testid="world-view-map"]')
      .catch(() => undefined);
    assert.notEqual(
      await p().evaluate(() => document.activeElement?.getAttribute('data-testid')),
      'world-view-map',
      'the Map control cannot take focus under the open popover',
    );
  },
  STEPS_MS,
);
