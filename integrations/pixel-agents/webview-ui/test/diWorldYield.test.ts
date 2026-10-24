/**
 * The world's CONTROLS and the Map across a changing window, through the ACTUAL App, in a real browser, over the SHIPPED living metropolis (a
 * large world, so the district controls and the Map exist) with a labelled FIXTURE office state. Nothing here is live Mission data and this is
 * integration over source (the App from Vite's dev server), not the production package.
 *
 * Proves, by driving the real sequence and the real keyboard rather than by reading comments:
 *  1. THE MAP SURVIVES THE WORLD YIELDING. The Map is open at a comfortable width; the window narrows until the world yields (its controls are
 *     removed, so the Map is not drawn); the window widens again, or the office is minimized: the Map is back, as it was left. While it is
 *     not drawn it must not own the keyboard (the Map installs a window-level key guard that swallows EVERY key: a Map left mounted but
 *     hidden would silently disable the keyboard).
 *  2. THE CONTROLS ARE OFFERED ONLY WHERE THEY FIT. For every window below, the expectation comes from diDockFor (the one place the arithmetic
 *     lives): where it says the world holds its controls, every control of the world is a tab stop that is FULLY drawn with a visible focus
 *     indicator (the real keyboard, diTabTraversal.ts); where it says the world cannot, the controls are inert and NOT in the tab sequence.
 *     So the controls are never enabled into a space they cannot occupy, and never left clipped but focusable.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an
 * explicit message.
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
import { diDockFor } from '../src/diPanelLayout.ts';
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
const MAP = '[data-testid="world-map"]';
const OPEN = '[data-testid="di-open-reception"]';
const INBOX = '[data-testid="di-reception-inbox"]';

/** Open the App at a window size, hand it the shipped world and the fixture's agents, and wait until it is drawn. */
async function boot(width: number, height: number, pageErrors?: string[]): Promise<void> {
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  // an exception thrown out of the game loop's render callback ends the loop for good, so page errors are collected from the very first frame
  if (pageErrors) page.on('pageerror', (e) => pageErrors.push(e.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify(LIVE));
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
const resize = async (width: number, height: number) => {
  await p().setViewportSize({ width, height });
  await p().waitForTimeout(600);
};
const minimizeOffice = async () => {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().waitForTimeout(400);
};
const restoreOffice = async () => {
  await p().click('[data-testid="di-office-panel-collapsed"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]', { state: 'detached' });
  await p().waitForTimeout(400);
};
/** A minimized surface puts a restore control in the strip: Reception is the cheapest. */
const minimizeReception = async () => {
  await p().click(OPEN);
  await p().waitForSelector(INBOX);
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  await p().waitForTimeout(300);
};
const gateStates = () =>
  p().evaluate(
    (sel) =>
      [...document.querySelectorAll(sel)].map(
        (e) =>
          `${e.getAttribute('data-di-world-controls')}${e.hasAttribute('inert') ? '+inert' : ''}`,
      ),
    GATE,
  );
const mapDrawn = () => p().locator(MAP).count();

/**
 * Do the page's own keydown handlers still receive a key? While the Map is OPEN its window-level capture guard swallows every key, so this is
 * false; with the Map closed or not drawn it must be true. (Teeth: it is asserted false with the Map open.)
 */
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
  'the Map across the REAL sequence: open at a comfortable width, the world yields, the window widens or the office is minimized: the Map comes back as it was left, and a Map that is not drawn does not own the keyboard',
  async () => {
    await boot(1440, 900);
    const steps: Array<{
      step: string;
      map: number;
      gates: string[];
      handlersReceiveKeys: boolean;
    }> = [];
    const observe = async (step: string) => {
      steps.push({
        step,
        map: await mapDrawn(),
        gates: await gateStates(),
        handlersReceiveKeys: await handlersReceiveKeys(),
      });
    };
    await p().click('[data-testid="world-view-map"]');
    await p().waitForSelector(MAP);
    await observe('1 the Map is opened at 1440 wide (office open: the world holds its controls)');
    await resize(400, 900);
    await observe('2 the window narrows to 400 (office open: the world yields entirely)');
    await resize(1440, 900);
    await observe('3 the window widens back to 1440');
    await resize(400, 900);
    await observe('4 the window narrows to 400 again');
    await minimizeOffice();
    await observe('5 the office is minimized at 400 (the world is 400 wide)');
    await restoreOffice();
    await observe('6 the office is restored at 400 (the world yields again)');
    await minimizeOffice();
    await observe('7 the office is minimized once more');
    console.log(`WORLD-YIELD MAP SEQUENCE ${JSON.stringify(steps)}`);

    const expected = [
      { controls: true, map: 1, keys: false },
      { controls: false, map: 0, keys: true },
      { controls: true, map: 1, keys: false },
      { controls: false, map: 0, keys: true },
      { controls: true, map: 1, keys: false },
      { controls: false, map: 0, keys: true },
      { controls: true, map: 1, keys: false },
    ];
    // the expectation for "do the controls fit" is the arithmetic's own, so a change to the threshold cannot make this test lie
    const dock = [
      diDockFor(1440, false, false),
      diDockFor(400, false, false),
      diDockFor(1440, false, false),
      diDockFor(400, false, false),
      diDockFor(400, true, true),
      diDockFor(400, false, false),
      diDockFor(400, true, true),
    ];
    steps.forEach((s, i) => {
      assert.equal(
        dock[i].worldControls,
        expected[i].controls,
        `${s.step}: the arithmetic offers the controls exactly where this sequence expects`,
      );
      assert.deepEqual(
        s.gates.every((g) => g === (expected[i].controls ? 'available' : 'yielded+inert')),
        true,
        `${s.step}: every world-controls gate is ${expected[i].controls ? 'available' : 'yielded and inert'} (saw ${JSON.stringify(s.gates)})`,
      );
      assert.equal(
        s.map,
        expected[i].map,
        `${s.step}: the Map is ${expected[i].map === 1 ? 'drawn (it is the state the person left)' : 'not drawn while its controls are removed'}`,
      );
      assert.equal(
        s.handlersReceiveKeys,
        expected[i].keys,
        `${s.step}: the page's key handlers ${expected[i].keys ? 'receive keys (a Map that is not drawn must not own the keyboard)' : 'are held by the open Map, as designed'}`,
      );
    });
  },
  STEPS_MS,
);

interface Case {
  width: number;
  height: number;
  office: 'open' | 'minimized';
  chip: boolean;
}
const CASES: Case[] = [
  { width: 320, height: 568, office: 'open', chip: false },
  { width: 320, height: 568, office: 'open', chip: true },
  { width: 320, height: 568, office: 'minimized', chip: false },
  { width: 320, height: 568, office: 'minimized', chip: true },
  { width: 360, height: 640, office: 'minimized', chip: true },
  { width: 414, height: 800, office: 'minimized', chip: true },
  { width: 464, height: 800, office: 'open', chip: true },
  { width: 640, height: 800, office: 'open', chip: false },
  { width: 700, height: 800, office: 'open', chip: false },
  { width: 780, height: 800, office: 'open', chip: false },
  { width: 900, height: 700, office: 'open', chip: false },
  { width: 1440, height: 900, office: 'open', chip: false },
];

/*
 * 2. COMPOSITION CONSISTENCY, one test per window. The expectation of "do the controls fit here" comes from diDockFor, the same function the
 * product consults, so this CANNOT falsify the contract by itself (if product and test both hid the controls it would pass); it proves that the
 * composition is consistent: wherever the arithmetic offers the controls they are fully drawn with a visible focus indicator, and wherever it does
 * not they are inert and out of the tab sequence. The CONTRACT is proved by the independent test below, which never asks diDockFor.
 */
for (const c of CASES) {
  const label = `${c.width}x${c.height} office ${c.office}${c.chip ? ' + strip' : ''}`;
  test(
    `SWEEP ${label}: the world controls are offered exactly where diDockFor says the world holds them (fully drawn, real keyboard), elsewhere inert and out of the tab sequence`,
    async () => {
      await boot(c.width, c.height);
      if (c.chip) await minimizeReception();
      if (c.office === 'minimized') await minimizeOffice();
      await showUpdateNotice();
      const dock = diDockFor(c.width, c.office === 'minimized', c.chip || c.office === 'minimized');
      const gates = await gateStates();
      const t = await tabTraversal(p(), {
        max: 400,
        regions: {
          world: GATE,
          panel: '[data-testid="di-office-panel"]',
          strip: '[data-testid="di-minimized-tray"]',
        },
      });
      const worldStops = t.stops.filter((s) => s.region === 'world');
      const problems = tabProblems(t, { world: 1, panel: 1, strip: 1, default: 1 });
      console.log(
        `SWEEP ROW ${label}: world ${dock.worldPx}px, controls ${dock.worldControls ? 'OFFERED' : 'removed'}; gates ${JSON.stringify(gates)}; ${t.stops.length} stops, ended ${t.ended}; world stops ${worldStops.map((s) => s.id).join(', ')}; problems ${JSON.stringify(problems.map((x) => `${x.id}: ${x.why.join(' / ')}`))}`,
      );
      assert.notEqual(t.ended, 'max', `${label}: the traversal reached the end of the sequence`);
      if (dock.worldControls) {
        assert.ok(
          gates.every((g) => g === 'available'),
          `${label}: the arithmetic offers the controls but a gate is ${JSON.stringify(gates)}`,
        );
        assert.ok(
          worldStops.length >= 3,
          `${label}: only ${worldStops.length} world tab stops (the toolbar alone has 3)`,
        );
        assert.ok(
          // the dismiss was identified by its text 'x' (the traversal identifier falls through to the text); it now carries an accessible name and a test id, which is what the identifier reads first
          worldStops.some((x) => x.id === 'version-notice-dismiss'),
          `${label}: the update notice's dismiss control is a tab stop`,
        );
      } else {
        assert.ok(
          gates.every((g) => g === 'yielded+inert'),
          `${label}: the arithmetic removes the controls but a gate is ${JSON.stringify(gates)}`,
        );
        assert.deepEqual(
          worldStops.map((x) => x.id),
          [],
          `${label}: no world control remains in the tab sequence`,
        );
      }
      assert.deepEqual(
        problems.map((x) => `${x.id}: ${x.why.join(' / ')}`),
        [],
        `${label}: every stop is fully drawn with a visible focus indicator`,
      );
    },
    STEPS_MS,
  );
}

/*
 * 3. THE CONTRACT, in rendered reality. It never consults diDockFor, the threshold or any gate attribute to decide what should be there: the
 * requirement is stated in this test. At 320 x 568, after the office is minimized (the 44 px strip is then present, so the world is 276 px
 * wide), the EXISTING world navigation is VISIBLE (real on-screen geometry, drawn on top where it sits) and KEYBOARD REACHABLE (the real Tab key
 * reaches it, with a focus indicator that paints), and it is OPERABLE (the Map opens by keyboard and closes again). It fails if the product hides
 * the controls, clips them, covers them or leaves them out of the tab sequence, whatever diDockFor says.
 */
const REQUIRED_WORLD_NAVIGATION = [
  'world-view-office',
  'world-view-deck',
  'world-view-woods',
  'world-view-map',
  'world-view-districts',
  'Layout',
  'city-button',
  'Settings',
];

test(
  'CONTRACT at 320 x 568, after Minimize, with the 44 px strip present: the existing world navigation is VISIBLE, reachable by the REAL KEYBOARD with a visible focus indicator, and operable [independent of diDockFor]',
  async () => {
    await boot(320, 568);
    await minimizeOffice();
    await showUpdateNotice();
    const strip = await p().evaluate(() => {
      const r = document
        .querySelector('[data-testid="di-minimized-tray"]')
        ?.getBoundingClientRect();
      return r ? { left: r.left, right: r.right, width: r.width } : null;
    });
    assert.ok(
      strip !== null &&
        strip.width >= 40 &&
        strip.width <= 48 &&
        strip.left >= 0 &&
        strip.right <= 320,
      `the ~44 px strip is really present and inside the window (saw ${JSON.stringify(strip)})`,
    );
    const t = await tabTraversal(p(), {
      max: 400,
      regions: { strip: '[data-testid="di-minimized-tray"]' },
    });
    assert.notEqual(t.ended, 'max');
    const failures: string[] = [];
    for (const id of REQUIRED_WORLD_NAVIGATION) {
      const stop = t.stops.find((x) => x.id === id);
      if (!stop) {
        failures.push(`${id}: not in the tab sequence at all`);
        continue;
      }
      const inside =
        stop.box.left >= -0.5 &&
        stop.box.right <= 320.5 &&
        stop.box.top >= -0.5 &&
        stop.box.bottom <= 568.5;
      if (!inside)
        failures.push(
          `${id}: its box ${Math.round(stop.box.left)}..${Math.round(stop.box.right)} x ${Math.round(stop.box.top)}..${Math.round(stop.box.bottom)} is not inside the 320 x 568 window`,
        );
      if (stop.hitSamples !== stop.sampled || stop.sampled === 0)
        failures.push(
          `${id}: only ${stop.hitSamples} of ${stop.sampled} points over it are drawn on it (covered by ${stop.coveredBy.join(', ') || 'nothing'})`,
        );
      if (stop.indicator.pixelsChanged !== true)
        failures.push(`${id}: focusing it paints no pixel (no visible focus indicator)`);
    }
    const dismiss = t.stops.find((x) => x.id === 'version-notice-dismiss');
    if (!dismiss || dismiss.hitSamples !== dismiss.sampled)
      failures.push(
        'the update notice is up and its dismiss control is not fully drawn and reachable',
      );
    assert.ok(
      t.stops.some(
        (x) =>
          x.id === 'di-office-panel-collapsed' &&
          x.region === 'strip' &&
          x.hitSamples === x.sampled,
      ),
      'the strip holds the office restore control, fully drawn, in the tab sequence',
    );
    assert.deepEqual(
      failures,
      [],
      'every required world navigation control is visible and reachable',
    );
    // OPERABLE by keyboard: Tab to the Map control and press Enter, the Map is drawn inside the window; Escape closes it
    await p().evaluate(() => {
      (document.activeElement as HTMLElement | null)?.blur?.();
      const s = document.createElement('span');
      s.tabIndex = -1;
      s.setAttribute('data-op-probe', 'true');
      document.body.prepend(s);
      s.focus();
    });
    let reached = false;
    for (let i = 0; i < 60 && !reached; i += 1) {
      await p().keyboard.press('Tab');
      reached = await p().evaluate(
        () => document.activeElement?.getAttribute('data-testid') === 'world-view-map',
      );
    }
    assert.ok(reached, 'the real Tab key reaches the Map control');
    await p().keyboard.press('Enter');
    await p().waitForSelector(MAP, { timeout: 10_000 });
    const map = await p().evaluate(() => {
      const r = document.querySelector('[data-testid="world-map"]')!.getBoundingClientRect();
      return { left: r.left, right: r.right, width: r.width, height: r.height };
    });
    assert.ok(
      map.width > 100 && map.height > 100 && map.left >= -0.5 && map.right <= 320.5,
      `the Map opened by keyboard is drawn inside the window (saw ${JSON.stringify(map)})`,
    );
    await p().keyboard.press('Escape');
    await p().waitForSelector(MAP, { state: 'detached', timeout: 10_000 });
  },
  STEPS_MS,
);

/*
 * THE WORLD CANVAS MUST SURVIVE BEING 0 PX WIDE. Where the office leaves the world no room (320 wide with the office open) the world's box is 0 px wide. The render
 * callback used to throw `drawImage ... canvas with a width or height of 0` there, and an exception out of that callback ends the game loop for good (the next frame
 * is only scheduled after render returns), so the canvas stayed BLANK after the world came back: minimized, or the window widened. Not a capture-timing artifact: every
 * pixel was zero at 1.2 s and at 6 s and after a resize event (smoke-candidate-5, canvas-frame-classification/). The test reads the pixels, not a flag.
 */
const canvasDrawn = () =>
  p().evaluate(() => {
    const c = [...document.querySelectorAll('canvas')].find(
      (x) => x.getClientRects().length > 0 && x.width > 0 && x.height > 0,
    );
    if (!c) return { size: null as number[] | null, drawnFraction: 0 };
    const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
    let drawn = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 0) drawn += 1;
    return {
      size: [c.width, c.height],
      drawnFraction: Math.round((drawn / (d.length / 4)) * 1000) / 1000,
    };
  });

test(
  'THE WORLD CANVAS SURVIVES BEING 0 PX WIDE: no page error while it is 0 wide, and it is DRAWN again when the world returns (Minimize, or a wider window)',
  async () => {
    const errors: string[] = [];
    await boot(320, 568, errors); // the office is open: the world is 0 px wide from the start
    await p().waitForTimeout(1500);
    assert.deepEqual(
      errors,
      [],
      'no exception while the world is 0 px wide (it ended the game loop)',
    );
    assert.equal(
      (await canvasDrawn()).size,
      null,
      'teeth: the world really is 0 px wide here (no drawable canvas)',
    );

    await minimizeOffice(); // the world is 276 px wide again
    await p().waitForTimeout(1500);
    const afterMinimize = await canvasDrawn();
    assert.ok(
      afterMinimize.size !== null && afterMinimize.size[0] > 200,
      `the canvas is back at its size (${JSON.stringify(afterMinimize.size)})`,
    );
    assert.ok(
      afterMinimize.drawnFraction > 0.9,
      `and it is DRAWN, not blank (drawn fraction ${afterMinimize.drawnFraction})`,
    );

    await restoreOffice(); // 0 px again
    await p().waitForTimeout(800);
    await minimizeOffice();
    await p().waitForTimeout(1500);
    assert.ok((await canvasDrawn()).drawnFraction > 0.9, 'and again after a second round trip');

    await restoreOffice();
    await p().setViewportSize({ width: 900, height: 568 }); // office open, world 480 wide
    await p().waitForTimeout(1500);
    const widened = await canvasDrawn();
    assert.ok(
      widened.size !== null && widened.drawnFraction > 0.9,
      `a wider window brings the world back drawn (${JSON.stringify(widened)})`,
    );
    assert.deepEqual(errors, [], 'and no exception at any step');
  },
  STEPS_MS,
);
