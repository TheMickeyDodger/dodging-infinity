/*
 * THE SHARED VERTICAL SPACE of the world's left strip, in the real App (WorldLeftRail), measured with the REAL KEYBOARD, and a wheel, at short heights.
 *
 * The camera-view controls (top-left) and the bottom-left column (the toolbar, the fixture label, the ambient legend) used to be placed independently, so a taller column met the
 * lower view stops: in a short window they were painted under the label and the legend (Atrium, Observatory, Map and Districts at 720 x 450 at 2x; Districts at 320 x 568). They now
 * SHARE one strip: the column is as tall as it is, the controls get what it leaves (never less than one stop), scrolling inside it, and where even that does not fit (400% zoom on a
 * 1280 x 900 window is 320 x 225) the strip itself scrolls, anchored so the column stays in view. The rule, per window:
 *   (a) the view controls never meet the column (their box and the column's box do not intersect), at any height;
 *   (b) REACHED AND VISIBLE, OR NEITHER: every one of the 7 stops is reached by Tab, forward and backward, and is fully drawn while it holds focus (a scrolled-out stop scrolls into
 *       view); the controls are NEVER removed for want of height. Where the width yields them (320 with the office open) NONE of their stops is in the tab order, and after the
 *       office is MINIMIZED they COME BACK: that is what separates a yield from a loss;
 *   (c) where the strip scrolls at rest (the window is shorter than the column plus one stop and the controls' offset), a WHEEL over a visible control scrolls it, and the first stop
 *       is then drawn: reachable at that size by scrolling within the UI, without zooming out or resizing the window.
 * LIMITS: the numbers hold for the windows listed (with and without the fixture banner at 320 x 225, the banner present elsewhere: the tallest column), the City popover closed. A sweep
 * of heights is not a proof for every height. The traversal id is a traversal identifier, not the accessible name. The wheel route is a scroll by the browser's own scroll chaining over
 * a pointer-events child; it is measured here in one engine (system Chrome).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { PNG } from 'pngjs';
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
  // an EXPLICIT fixture preview (the floating fixture banner and the in-panel note exist only then)
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
const minimizeOffice = async () => {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().waitForTimeout(400);
};
const pause = (ms: number) => p().waitForTimeout(ms);
/** A fixture label is PAINTED when it is CSS-visible, its whole box is inside the window, and every hit-test point over it (pointer-events neutralized) lands on it. */

interface View {
  label: string;
  width: number;
  height: number;
  scale: number;
  fixture: boolean;
  /** The width yields the controls while the office is open (320): they must be absent from the tab order then and COME BACK once it is minimized. */
  yieldFirst: boolean;
}
const VIEWS: View[] = [
  {
    label: '1100 x 600 (fixture banner present)',
    width: 1100,
    height: 600,
    scale: 1,
    fixture: true,
    yieldFirst: false,
  },
  {
    label: '1100 x 450 (fixture banner present)',
    width: 1100,
    height: 450,
    scale: 1,
    fixture: true,
    yieldFirst: false,
  },
  {
    label: '1100 x 360 (fixture banner present)',
    width: 1100,
    height: 360,
    scale: 1,
    fixture: true,
    yieldFirst: false,
  },
  {
    label:
      '720 x 450 at 2x (ZOOM-EQUIVALENT, not a native Command-plus test; fixture banner present)',
    width: 720,
    height: 450,
    scale: 2,
    fixture: true,
    yieldFirst: false,
  },
  {
    label: '320 x 568 (fixture banner present)',
    width: 320,
    height: 568,
    scale: 1,
    fixture: true,
    yieldFirst: true,
  },
  {
    label: '1100 x 300 (fixture banner present: the City test of the same size has none)',
    width: 1100,
    height: 300,
    scale: 1,
    fixture: true,
    yieldFirst: false,
  },
  {
    label: '1100 x 256 (fixture banner present)',
    width: 1100,
    height: 256,
    scale: 1,
    fixture: true,
    yieldFirst: false,
  },
  {
    label:
      '320 x 225 (fixture banner present): 400% ZOOM-EQUIVALENT on a 1280 x 900 window, not a native zoom test',
    width: 320,
    height: 225,
    scale: 1,
    fixture: true,
    yieldFirst: true,
  },
  {
    label:
      '320 x 225 (no fixture banner): 400% ZOOM-EQUIVALENT on a 1280 x 900 window, not a native zoom test',
    width: 320,
    height: 225,
    scale: 1,
    fixture: false,
    yieldFirst: true,
  },
];
const STOPS = [
  'world-view-office',
  'world-view-deck',
  'world-view-woods',
  'world-view-office-atrium',
  'world-view-office-observatory',
  'world-view-map',
  'world-view-districts',
];
const REGIONS = {
  zoom: '[data-testid="world-zoom-buttons"]',
  cluster: '[data-testid="world-view-controls"]',
};

const clusterFacts = () =>
  p().evaluate(() => {
    const cluster = document.querySelector(
      '[data-testid="world-view-controls"]',
    ) as HTMLElement | null;
    const stack = document.querySelector('[data-testid="di-world-stack"]') as HTMLElement | null;
    const rail = document.querySelector('[data-testid="di-world-rail"]') as HTMLElement | null;
    const box = (e: Element | null) =>
      e
        ? (({ left, top, right, bottom }) => ({ left, top, right, bottom }))(
            e.getBoundingClientRect(),
          )
        : null;
    const meets = (a: ReturnType<typeof box>, b: ReturnType<typeof box>) =>
      !!a &&
      !!b &&
      !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
    const c = box(cluster);
    const zoom = document.querySelector('[data-testid="world-zoom-buttons"]');
    return {
      zoomBox: box(zoom),
      // a disabled button (the zoom is at its limit) is correctly not a tab stop: the expectation is every ENABLED one
      zoomEnabled: document.querySelectorAll(
        '[data-testid="world-zoom-buttons"] button:not([disabled])',
      ).length,
      zoomMeetsCluster:
        zoom !== null &&
        cluster !== null &&
        getComputedStyle(cluster).visibility !== 'hidden' &&
        meets(box(zoom), c),
      zoomMeetsColumn: zoom !== null && meets(box(zoom), box(stack)),
      clusterPresent: cluster !== null,
      clusterHidden: cluster !== null && getComputedStyle(cluster).visibility === 'hidden',
      gate:
        cluster?.closest('[data-di-world-controls]')?.getAttribute('data-di-world-controls') ??
        null,
      clusterBox: c,
      stackBox: box(stack),
      clusterMeetsColumn:
        cluster !== null &&
        getComputedStyle(cluster).visibility !== 'hidden' &&
        meets(c, box(stack)),
      railScrolls: rail ? rail.scrollHeight > rail.clientHeight + 1 : null,
      railScrollTop: rail ? Math.round(rail.scrollTop) : null,
      railRange: rail ? Math.round(rail.scrollHeight - rail.clientHeight) : null,
    };
  });

for (const v of VIEWS) {
  test(
    `THE LEFT STRIP SHARES ITS HEIGHT at ${v.label}: the view controls never meet the column, are never removed for want of height, and every stop is reached and drawn by keyboard (forward and back)${v.yieldFirst ? ', after the width yield comes back on Minimize' : ''}`,
    async () => {
      await boot(v.width, v.height, { fixture: v.fixture, scale: v.scale });
      const failures: string[] = [];
      const rows: string[] = [];
      if (v.yieldFirst) {
        // THE SEPARATOR OF A YIELD FROM A LOSS: with the office OPEN at this width the world yields its controls (inert and hidden, none in the tab order)...
        const before = await clusterFacts();
        const t0 = await tabTraversal(p(), { max: 220, regions: REGIONS });
        const stops0 = t0.stops.filter((s) => s.region === 'cluster').length;
        rows.push(
          `office open: gate ${before.gate}, controls hidden ${before.clusterHidden}, ${stops0} of their stops in the tab order`,
        );
        if (before.gate !== 'yielded')
          failures.push(
            `office open: the controls are expected to be yielded by the width (gate ${before.gate})`,
          );
        if (stops0 > 0)
          failures.push(
            `office open: the controls are yielded but ${stops0} of their stops are still in the tab order`,
          );
      }
      await minimizeOffice();
      await p().waitForSelector('[data-testid="ambient-legend"]', {
        state: 'attached',
        timeout: 30_000,
      });
      await pause(500);
      const facts = await clusterFacts();
      if (!facts.clusterPresent)
        failures.push(
          'the camera-view controls are not in the page at all (the large world has them): the check would be vacuous',
        );
      // ...and they COME BACK once the office is minimized, at this size: never removed for want of height
      if (facts.clusterHidden)
        failures.push(
          `the camera-view controls are REMOVED at ${v.label} (gate ${facts.gate}) after the office is minimized: that is loss, not a yield`,
        );
      if (facts.clusterMeetsColumn)
        failures.push(
          `(a) the view controls ${JSON.stringify(facts.clusterBox)} meet the column ${JSON.stringify(facts.stackBox)}`,
        );
      // the zoom buttons are part of the same strip, so a strip that scrolls must not carry the controls over them (the first version of the strip did, by 17 px at 1100 x 600)
      if (facts.zoomMeetsCluster)
        failures.push(
          `(a) the zoom buttons ${JSON.stringify(facts.zoomBox)} meet the view controls ${JSON.stringify(facts.clusterBox)}`,
        );
      if (facts.zoomMeetsColumn)
        failures.push(
          `(a) the zoom buttons ${JSON.stringify(facts.zoomBox)} meet the column ${JSON.stringify(facts.stackBox)}`,
        );
      rows.push(
        `${v.label}: controls ${facts.clusterHidden ? 'REMOVED' : 'shown'}, cluster box ${JSON.stringify(facts.clusterBox)}, column ${JSON.stringify(facts.stackBox)}, strip scrolls at rest ${facts.railScrolls} (scrollTop ${facts.railScrollTop} of ${facts.railRange})`,
      );
      for (const backward of [false, true]) {
        const t = await tabTraversal(p(), { max: 220, regions: REGIONS, backward });
        const inCluster = t.stops.filter((s) => s.region === 'cluster');
        const inZoom = t.stops.filter((s) => s.region === 'zoom');
        const ids = inCluster.map((s) => s.id);
        rows.push(
          `${backward ? 'backward' : 'forward'}: ${t.stops.length} stops (ended ${t.ended}), ${inZoom.length} zoom buttons, ${inCluster.length} in the view controls: ${ids.join(', ') || 'none'}`,
        );
        if (facts.zoomEnabled < 1)
          failures.push('the strip has no enabled zoom button: the check would be vacuous');
        if (inZoom.length !== facts.zoomEnabled)
          failures.push(
            `(b) ${backward ? 'backward' : 'forward'}: ${inZoom.length} zoom buttons are reached by Tab and ${facts.zoomEnabled} are enabled`,
          );
        for (const x of tabProblems({ stops: inZoom, ended: t.ended }, 1))
          failures.push(
            `(b) ${backward ? 'backward' : 'forward'}: a zoom button (${x.id}) is reached but not fully drawn while focused: ${x.why.join('; ')}`,
          );
        if (t.ended === 'max')
          failures.push(
            `${backward ? 'backward' : 'forward'}: the traversal hit its cap (incomplete)`,
          );
        for (const stop of STOPS)
          if (!ids.includes(stop))
            failures.push(
              `(b) ${backward ? 'backward' : 'forward'}: ${stop} is not reached by Tab`,
            );
        for (const x of tabProblems({ stops: inCluster, ended: t.ended }, 1))
          failures.push(
            `(b) ${backward ? 'backward' : 'forward'}: ${x.id} is reached but not fully drawn while focused: ${x.why.join('; ')}`,
          );
      }
      // (c) the pointer route where the strip scrolls at rest: a wheel over a visible control scrolls it, and the first stop is then drawn
      const rest = await clusterFacts();
      if (rest.railScrolls) {
        await p().evaluate(() => {
          const r = document.querySelector('[data-testid="di-world-rail"]') as HTMLElement;
          r.scrollTop = r.scrollHeight;
        });
        await pause(200);
        const city = await p().locator('[data-testid="city-button"]').boundingBox();
        if (city === null)
          failures.push('(c) the City button is not drawn at rest: nothing visible to wheel over');
        else {
          const before = (await clusterFacts()).railScrollTop;
          await p().mouse.move(city.x + city.width / 2, city.y + city.height / 2);
          await p().mouse.wheel(0, -800);
          await pause(400);
          const after = await clusterFacts();
          rows.push(
            `wheel over the City button: strip scrollTop ${before} -> ${after.railScrollTop}`,
          );
          if (!(after.railScrollTop !== null && before !== null && after.railScrollTop < before))
            failures.push(
              `(c) a wheel over a visible control did not scroll the strip (scrollTop ${before} -> ${after.railScrollTop})`,
            );
          const painted = await p().evaluate(() => {
            const forced = document.createElement('style');
            forced.textContent = '* { pointer-events: auto !important; }';
            document.head.append(forced);
            try {
              const el = document.querySelector('[data-testid="world-view-office"]') as HTMLElement;
              const r = el.getBoundingClientRect();
              const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return {
                onScreen: r.top >= 0 && r.bottom <= innerHeight,
                onTop: !!top && (el === top || el.contains(top)),
              };
            } finally {
              forced.remove();
            }
          });
          rows.push(
            `after the wheel, the first stop (Office): on screen ${painted.onScreen}, drawn on top ${painted.onTop}`,
          );
          if (!painted.onScreen || !painted.onTop)
            failures.push(
              `(c) after scrolling the strip by wheel the first stop is not drawn (on screen ${painted.onScreen}, on top ${painted.onTop})`,
            );
        }
      }
      console.log(`SHARED SPACE ROW\n${rows.join('\n')}`);
      assert.deepEqual(
        failures,
        [],
        `${v.label}: the left strip shares its height (see the rows above)`,
      );
    },
    STEPS_MS,
  );
}

/*
 * THE CITY POPOVER SHARES THE STRIP, as INVARIANTS: it is constrained to the ACTUAL first row (never taller than the room above the column, never spilling into the column), at its correct width (left 10 px and no
 * wider than the column's 266 px cap: no double offset), and it PAINTS ABOVE whatever sits in the first row (the camera controls and zoom buttons are drawn but suspended while it is open, and must not show over it).
 * The City contract (livingCityApp.test.ts: the panel inside the viewport, the heading and Close visible, the intro fitting at the top) is graded THERE, unchanged; this adds what that test cannot see: row 2 exposure and
 * the paint order. A constant-height panel anchored at the bottom (891 px at every window, top -13 at 1600 x 1000 and -713 at 1100 x 300) was the defect. The paint invariant is DECODED PIXELS: the popover's visible
 * region compared as is and with the whole first-row cell hidden (it must not change a pixel), with a positive companion (hiding the popover itself must change many), so a pass cannot come from measuring nothing.
 * LIMITS: the windows below; fixture banner present where stated (the tallest column); at windows shorter than the column plus the first row's floor the strip itself scrolls and the contract is not asserted there.
 */
interface CityView {
  label: string;
  width: number;
  height: number;
  scale: number;
  fixture: boolean;
  /** Minimize the office first (always where the City button is unavailable with it open, as the City tests do). */
  minimize: boolean;
}
const CITY_VIEWS: CityView[] = [
  {
    label: '1440 x 900 (fixture banner present, office minimized)',
    width: 1440,
    height: 900,
    scale: 1,
    fixture: true,
    minimize: true,
  },
  {
    label: '1600 x 1000 (office open)',
    width: 1600,
    height: 1000,
    scale: 1,
    fixture: false,
    minimize: false,
  },
  {
    label: '1100 x 300 (office open, no banner)',
    width: 1100,
    height: 300,
    scale: 1,
    fixture: false,
    minimize: false,
  },
  {
    label: '640 x 360 (office minimized, no banner)',
    width: 640,
    height: 360,
    scale: 1,
    fixture: false,
    minimize: true,
  },
  {
    label:
      '720 x 450 at 2x (ZOOM-EQUIVALENT, not a native Command-plus test; fixture banner present, office minimized)',
    width: 720,
    height: 450,
    scale: 2,
    fixture: true,
    minimize: true,
  },
  {
    label: '320 x 568 (fixture banner present, office minimized)',
    width: 320,
    height: 568,
    scale: 1,
    fixture: true,
    minimize: true,
  },
];
for (const v of CITY_VIEWS) {
  test(
    `THE CITY POPOVER SHARES THE STRIP at ${v.label}: inside the viewport with its heading and Close, never meeting the column, at its correct width, and painted above the first row's controls (decoded pixels)`,
    async () => {
      await boot(v.width, v.height, { fixture: v.fixture, scale: v.scale });
      if (v.minimize) await minimizeOffice();
      await p().waitForSelector('[data-testid="ambient-legend"]', {
        state: 'attached',
        timeout: 30_000,
      });
      await pause(400);
      await p().focus('[data-testid="city-button"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('#city-controls');
      await pause(600);
      const f = await p().evaluate(() => {
        const box = (e: Element | null) =>
          e
            ? (({ left, top, right, bottom }) => ({ left, top, right, bottom }))(
                e.getBoundingClientRect(),
              )
            : null;
        const pop = document.querySelector('#city-controls') as HTMLElement;
        const scroll = document.querySelector(
          '[data-testid="city-controls-scroll"]',
        ) as HTMLElement;
        return {
          vw: innerWidth,
          vh: innerHeight,
          popover: box(pop),
          heading: box(document.querySelector('[data-testid="city-controls-heading"]')),
          close: box(document.querySelector('[data-testid="city-controls-close"]')),
          column: box(document.querySelector('[data-testid="di-world-stack"]')),
          ownScrollRegionScrolls: scroll.scrollHeight > scroll.clientHeight + 1,
        };
      });
      const inside = (b: { left: number; top: number; right: number; bottom: number } | null) =>
        !!b && b.top >= -0.5 && b.left >= -0.5 && b.bottom <= f.vh + 0.5 && b.right <= f.vw + 0.5;
      const failures: string[] = [];
      if (!inside(f.popover))
        failures.push(
          `(1) the panel ${JSON.stringify(f.popover)} is not inside the ${f.vw} x ${f.vh} viewport`,
        );
      if (!inside(f.heading) || !inside(f.close))
        failures.push(
          `(2) the heading ${JSON.stringify(f.heading)} and Close ${JSON.stringify(f.close)} are not both inside the viewport`,
        );
      const meets = (a: NonNullable<typeof f.popover>, b: NonNullable<typeof f.column>) =>
        !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
      if (f.popover && f.column && meets(f.popover, f.column))
        failures.push(
          `(row 2) the panel ${JSON.stringify(f.popover)} meets the column (toolbar, label, legend) ${JSON.stringify(f.column)}`,
        );
      if (f.popover && Math.abs(f.popover.left - 10) > 0.5)
        failures.push(
          `(width) the panel's left edge is ${f.popover.left}, not 10 (a double offset narrows it)`,
        );
      if (f.popover && f.popover.right - f.popover.left > 266.5)
        failures.push(
          `(width) the panel is ${f.popover.right - f.popover.left} px wide, over the 266 px column cap`,
        );
      // PAINT ORDER, in decoded pixels: the popover's VISIBLE region as is, with the first-row cell hidden (the camera controls and zoom buttons live in it), and with the popover itself hidden
      if (f.popover) {
        const x = Math.max(0, Math.floor(f.popover.left));
        const y = Math.max(0, Math.floor(f.popover.top));
        const w = Math.min(f.vw, Math.ceil(f.popover.right)) - x;
        const h = Math.min(f.vh, Math.ceil(f.popover.bottom)) - y;
        if (w * h < 5000)
          failures.push(
            `(paint) only ${w * h} px of the panel are visible: too little for the paint invariant to mean anything (the limit of this measure)`,
          );
        else {
          const shoot = async () =>
            PNG.sync.read(
              await p().screenshot({ clip: { x, y, width: w, height: h }, animations: 'disabled' }),
            );
          const diff = (a: PNG, b: PNG) => {
            let n = 0;
            for (let i = 0; i < a.data.length; i += 4) {
              let d = 0;
              for (let k = 0; k < 4; k += 1)
                d = Math.max(d, Math.abs(a.data[i + k] - b.data[i + k]));
              if (d > 0) n += 1;
            }
            return n;
          };
          const asIs = await shoot();
          const hideCell = await p().addStyleTag({
            content: '.di-rail-top { visibility: hidden !important; }',
          });
          await pause(250);
          const cellHidden = await shoot();
          await hideCell.evaluate((e) => (e as Element).remove());
          const hidePop = await p().addStyleTag({
            content: '#city-controls { visibility: hidden !important; }',
          });
          await pause(250);
          const popHidden = await shoot();
          await hidePop.evaluate((e) => (e as Element).remove());
          const overCity = diff(asIs, cellHidden);
          const companion = diff(asIs, popHidden);
          console.log(
            `CITY SHARES ${v.label}: popover ${JSON.stringify(f.popover)}, column ${JSON.stringify(f.column)}, own scroll region scrolls ${f.ownScrollRegionScrolls}; pixels changed in the panel's visible ${w} x ${h} by hiding the first-row cell: ${overCity}; by hiding the panel itself (positive companion): ${companion}`,
          );
          if (companion < 1000)
            failures.push(
              `(paint) hiding the panel itself changed only ${companion} pixels in its own region: the paint measure could not see anything (a vacuous pass is not allowed)`,
            );
          if (overCity > 0)
            failures.push(
              `(paint) ${overCity} pixels of the panel's visible region are painted by what is in the first-row cell (the camera controls or zoom buttons show over the City panel)`,
            );
        }
      }
      assert.deepEqual(failures, [], `${v.label}: the City popover shares the strip`);
    },
    STEPS_MS,
  );
}

/*
 * THE CITY AT 320 x 225 (400% zoom on a 1280 x 900 window, a ZOOM-EQUIVALENT, not a native zoom test): a CONSTRAINED-HEIGHT case of its own. The six-window City contract above is NOT
 * cited as covering it, and simultaneous display of everything is not asked for here. TWO DIFFERENT PROPERTIES, in separate tests, and neither discharges the other:
 *   (1) OPENING RESPONSE: pressing City, by the REAL mouse and by the REAL keyboard, shows the panel (its header and Close) on screen WHEN IT OPENS, with no later Tab or scroll;
 *   (2) RECOVERY: with the panel open, every one of its controls is a Tab stop forward and backward, fully drawn and fully inside the window while it holds focus.
 * Reachable by later navigation is not revealed on open. Opening also asserts that the control which HOLDS FOCUS at open is on screen and reached by a pointer at its centre (the focus
 * indicator's own painting is covered by the tab traversal's indicator check, not here): a reveal must not trade an invisible panel for an invisible focus. LIMITS: system Chrome; with and without the fixture label; the wait for the revealed state is on the state itself and the final
 * assertions are on what was measured (nothing else acts in the test: no Tab, no scroll input, no focus move before the measurement); the strip is not re-scrolled if its content later
 * changes height, and that is not covered.
 */
const OPENING_WINDOWS = [
  { fixture: true, label: 'with the fixture label' },
  { fixture: false, label: 'without the label' },
] as const;
async function bootCity225(fixture: boolean) {
  await boot(320, 225, { fixture });
  await minimizeOffice();
  await p().waitForSelector('[data-testid="ambient-legend"]', {
    state: 'attached',
    timeout: 30_000,
  });
  await pause(400);
}
for (const o of OPENING_WINDOWS) {
  for (const how of ['mouse', 'keyboard'] as const) {
    test(
      `THE CITY OPENS INTO VIEW at 320 x 225 ${o.label}, by real ${how} activation: header and Close are on screen and reachable with no later Tab or scroll, the control holding focus is on screen, and Escape restores focus to the City button on screen`,
      async () => {
        await bootCity225(o.fixture);
        const failures: string[] = [];
        const premise = await p().evaluate(() => {
          const rail = document.querySelector('[data-testid="di-world-rail"]') as HTMLElement;
          return { range: rail.scrollHeight - rail.clientHeight, vh: innerHeight, vw: innerWidth };
        });
        // the premise is MEASURED, not assumed: the strip scrolls at this height (otherwise this test would not be about the constrained case)
        assert.ok(
          premise.range > 0,
          `the strip scrolls at rest at 320 x 225 ${o.label} (range ${premise.range}); a test of the constrained case needs it`,
        );
        const cityBox = await p().locator('[data-testid="city-button"]').boundingBox();
        assert.ok(
          cityBox !== null && cityBox.y >= 0 && cityBox.y + cityBox.height <= premise.vh,
          `the City button is on screen to be pressed (${JSON.stringify(cityBox)})`,
        );
        if (how === 'keyboard') {
          await p().focus('[data-testid="city-button"]');
          await p().keyboard.press('Enter');
        } else {
          const cx = cityBox.x + cityBox.width / 2;
          const cy = cityBox.y + cityBox.height / 2;
          const lands = await p().evaluate(
            ({ x, y }) =>
              document.elementFromPoint(x, y)?.closest('[data-testid="city-button"]') !== null,
            { x: cx, y: cy },
          );
          assert.ok(
            lands,
            'the pointer is over the City button when it is pressed (a click on nothing opens nothing)',
          );
          await p().mouse.click(cx, cy);
        }
        await p().waitForSelector('#city-controls');
        // wait for the revealed STATE (not a frame count): header and Close fully inside the window. A defect never reaches it, and the assertions below judge what was measured.
        await p()
          .waitForFunction(
            () => {
              const inside = (sel: string) => {
                const b = document.querySelector(sel)?.getBoundingClientRect();
                return (
                  !!b &&
                  b.top >= 0 &&
                  b.bottom <= innerHeight &&
                  b.left >= 0 &&
                  b.right <= innerWidth
                );
              };
              return (
                inside('[data-testid="city-controls-heading"]') &&
                inside('[data-testid="city-controls-close"]')
              );
            },
            null,
            { timeout: 5000 },
          )
          .catch(() => undefined);
        const s = await p().evaluate(() => {
          const box = (sel: string) => {
            const e = document.querySelector(sel);
            if (!e) return null;
            const b = e.getBoundingClientRect();
            return {
              top: Math.round(b.top),
              bottom: Math.round(b.bottom),
              left: Math.round(b.left),
              right: Math.round(b.right),
              cx: (b.left + b.right) / 2,
              cy: (b.top + b.bottom) / 2,
            };
          };
          const hits = (sel: string) => {
            const b = document.querySelector(sel)?.getBoundingClientRect();
            if (!b) return false;
            return (
              document
                .elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2)
                ?.closest(sel) != null
            );
          };
          const ae = document.activeElement as HTMLElement | null;
          const fb = ae && ae !== document.body ? ae.getBoundingClientRect() : null;
          return {
            vw: innerWidth,
            vh: innerHeight,
            // THE FOCUSED CONTROL AT OPEN: the reveal must not exchange an invisible panel for an invisible active focus indicator (focus stays on the opener, which sits in the strip's second row)
            focusedBox: fb
              ? {
                  top: Math.round(fb.top),
                  bottom: Math.round(fb.bottom),
                  left: Math.round(fb.left),
                  right: Math.round(fb.right),
                }
              : null,
            focusedHit:
              fb !== null &&
              ae!.contains(
                document.elementFromPoint((fb.left + fb.right) / 2, (fb.top + fb.bottom) / 2),
              ),
            popover: box('#city-controls'),
            heading: box('[data-testid="city-controls-heading"]'),
            close: box('[data-testid="city-controls-close"]'),
            headingHit: hits('[data-testid="city-controls-heading"]'),
            closeHit: hits('[data-testid="city-controls-close"]'),
            focused: ae ? (ae.getAttribute('data-testid') ?? ae.tagName.toLowerCase()) : null,
          };
        });
        console.log(`CITY OPENS (225, ${o.label}, ${how}): ${JSON.stringify(s)}`);
        for (const [name, b] of [
          ['heading', s.heading],
          ['Close', s.close],
          ['panel', s.popover],
        ] as const) {
          if (b === null) failures.push(`${name} is not in the page`);
          else if (b.top < 0 || b.bottom > s.vh || b.left < 0 || b.right > s.vw)
            failures.push(
              `${name} is not fully on screen when the City opens (top ${b.top}, bottom ${b.bottom} in a ${s.vh} px window)`,
            );
        }
        if (!s.headingHit)
          failures.push('the heading is on screen but something else is at its centre');
        if (!s.closeHit)
          failures.push('Close is on screen but a pointer at its centre does not reach it');
        if (s.focusedBox === null)
          failures.push(
            `nothing is focused at open (the active element is the page body), so a visible focus indicator cannot be asserted: ${s.focused}`,
          );
        else if (
          s.focusedBox.top < 0 ||
          s.focusedBox.bottom > s.vh ||
          s.focusedBox.left < 0 ||
          s.focusedBox.right > s.vw
        )
          failures.push(
            `the FOCUSED control (${s.focused}) is not fully on screen at open (top ${s.focusedBox.top}, bottom ${s.focusedBox.bottom} in a ${s.vh} px window): the reveal exchanged an invisible panel for an invisible active focus`,
          );
        else if (!s.focusedHit)
          failures.push(
            `the focused control (${s.focused}) is on screen but a pointer at its centre does not reach it`,
          );
        // CLOSE FOCUS BEHAVIOUR preserved: Escape closes the panel and focus is restored to the control that held it before opening (the City button), on screen, never the page body
        // pressed where opening LEFT focus (the City button, or the panel's Close when the reveal carried the opener out of view): nothing is re-focused first
        await p().keyboard.press('Escape');
        await p().waitForSelector('#city-controls', { state: 'detached', timeout: 5000 });
        const back = await p().evaluate(() => {
          const ae = document.activeElement as HTMLElement | null;
          const b = ae?.getBoundingClientRect();
          return {
            focused: ae ? (ae.getAttribute('data-testid') ?? ae.tagName.toLowerCase()) : null,
            onScreen: !!b && b.top >= 0 && b.bottom <= innerHeight,
          };
        });
        if (back.focused !== 'city-button' || !back.onScreen)
          failures.push(
            `after Escape focus is on ${back.focused} (on screen: ${back.onScreen}), not the City button on screen`,
          );
        assert.deepEqual(
          failures,
          [],
          `the City opens into view at 320 x 225 ${o.label} by ${how}`,
        );
      },
      STEPS_MS,
    );
  }

  test(
    `THE CITY AT 320 x 225 ${o.label}, RECOVERY (a different property from opening): every control of the open panel, enumerated from the page, is a Tab stop forward and backward, fully inside the window and fully drawn while focused`,
    async () => {
      await bootCity225(o.fixture);
      await p().focus('[data-testid="city-button"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('#city-controls');
      await pause(400);
      const expected = await p().evaluate(
        () =>
          document.querySelectorAll('#city-controls button:not([disabled]), #city-controls summary')
            .length,
      );
      assert.ok(expected >= 15, `the panel's controls are enumerated from the page (${expected})`);
      const failures: string[] = [];
      for (const backward of [false, true]) {
        const t = await tabTraversal(p(), {
          max: 220,
          regions: { city: '#city-controls' },
          backward,
        });
        const stops = t.stops.filter((x) => x.region === 'city');
        if (stops.length !== expected)
          failures.push(
            `${backward ? 'backward' : 'forward'}: ${stops.length} City stops reached, ${expected} controls on the page`,
          );
        for (const x of tabProblems({ stops, ended: t.ended }, 1))
          failures.push(`${backward ? 'backward' : 'forward'}: ${x.id}: ${x.why.join('; ')}`);
        const vp = p().viewportSize()!;
        for (const x of stops)
          if (x.box.top < 0 || x.box.bottom > vp.height || x.box.left < 0 || x.box.right > vp.width)
            failures.push(
              `${backward ? 'backward' : 'forward'}: ${x.id} is not fully inside the window while focused (top ${Math.round(x.box.top)}, bottom ${Math.round(x.box.bottom)})`,
            );
      }
      assert.deepEqual(failures, [], `recovery at 320 x 225 ${o.label}`);
    },
    STEPS_MS,
  );
}
