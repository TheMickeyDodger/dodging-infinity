/**
 * LIVING CITY 6–9, moving traffic through the ACTUAL App, in a real browser, over the SHIPPED living metropolis.
 *
 * WHAT THIS EVIDENCE IS, EXACTLY: real-App INTEGRATION OVER SOURCE. The App is mounted from source by Vite's dev
 * server (`test/harness/di-app.html` -> `src/main.tsx`); it is NOT the production package and this file is NOT
 * production-package browser proof. The office state the agents come from is a labelled FIXTURE
 * (`di-office-state-transitions-v2.FIXTURE.json`); nothing here is evidence of a live provider or Herdr. The
 * preference store is the real persistence module under a temp root this test owns.
 *
 * HOW MOTION IS PROVED, not asserted. Static art or a moving camera would not count, so the proof is on the
 * canvas pixels themselves: the screen is calibrated against the real mouse (the App's own hover tile gives the
 * tile size and offset exactly), each vehicle's pose is read from the App's own OfficeState, and the vehicle's
 * ACTUAL sprite PNG (the world's own art) is matched, pixel for pixel, against the canvas at the position the
 * pose predicts. A vehicle counts as drawn only when its sprite is found there; it counts as moving only when
 * it is found at two different places a moment apart.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither,
 * every test is skipped with an explicit message.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
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
import { AgentStateStore } from '../../server/src/agentStateStore.ts';
import { CITY_PRESENTATION_FILE_NAME } from '../../server/src/constants.ts';
import { readCityPresentation } from '../../server/src/di/cityPresentationPersistence.ts';
import { handleDiClientMessage } from '../../server/src/di/diClientMessages.ts';
import { resetStateRootForTests, setStateRoot } from '../../server/src/stateRoot.ts';
import { CITY_OVERCAST_RAIN } from '../src/constants.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** The EXISTING screenshot seam: set DI_REVIEW_SHOTS=<dir> to also save the views. FIXTURE data only. */
const SHOTS = process.env['DI_REVIEW_SHOTS'] ?? null;
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'),
    'utf-8',
  ),
) as {
  states: Record<'live', DiOfficeState>;
};
const LIVE = fixture.states.live;
const metropolis = fs.readFileSync(
  path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'),
  'utf-8',
);
const FURNITURE = path.join(root, 'public/assets/furniture');

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: WebSocket | null = null;
let connected: Promise<WebSocket> = Promise.resolve(null as unknown as WebSocket);
let resolveConnected: (ws: WebSocket) => void = () => undefined;
let sandbox = '';
let saves: Array<Record<string, unknown>> = [];
const ctxBase = { store: new AgentStateStore(), cache: {} as never, extensionVersion: 'test' };

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    const send = (m: Record<string, unknown>) =>
      ws.readyState === ws.OPEN && ws.send(JSON.stringify(m));
    if (msg.type === 'webviewReady') {
      send({ type: 'cityPresentationLoaded', state: readCityPresentation() });
      socket = ws;
      resolveConnected(ws);
    } else if (msg.type === 'saveCityPresentation') {
      saves.push(msg);
      handleDiClientMessage(msg, send, ctxBase);
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
  resetStateRootForTests();
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true });
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (living city traffic): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true });
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-living-traffic-')));
  resetStateRootForTests();
  setStateRoot(path.join(sandbox, 'di-root'));
  saves = [];
}, STEPS_MS);

type Reduced = 'reduce' | 'no-preference';
const p = () => page!;

async function boot(reducedMotion: Reduced = 'no-preference'): Promise<void> {
  await page?.close();
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.emulateMedia({ reducedMotion });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify(LIVE));
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
  await p().waitForFunction(
    () => {
      const o = (
        window as unknown as {
          __diHarnessOffice?: {
            characters: Map<number, unknown>;
            layout: { cols: number };
            getTraffic(): { vehicles: unknown[] } | null;
          };
        }
      ).__diHarnessOffice;
      return o !== undefined && o.characters.size >= 4 && o.layout.cols === 352;
    },
    undefined,
    { timeout: 90_000 },
  );
  await settle();
}
/** Until the street is alive: at least `n` vehicles driving at speed. */
const waitForDriving = (n = 12) =>
  p().waitForFunction(
    (need) => {
      const o = (
        window as unknown as {
          __diHarnessOffice?: {
            getTraffic(): { vehicles: Array<{ state: string; v: number }> } | null;
          };
        }
      ).__diHarnessOffice;
      const t = o?.getTraffic();
      return !!t && t.vehicles.filter((v) => v.state === 'drive' && v.v > 1).length >= need;
    },
    n,
    { timeout: 60_000 },
  );
/** Until the traffic has been built (it is not, when a saved choice is Off). */
const waitForBuilt = () =>
  p().waitForFunction(
    () => {
      const o = (
        window as unknown as {
          __diHarnessOffice?: { getTraffic(): { vehicles: unknown[] } | null };
        }
      ).__diHarnessOffice;
      return (o?.getTraffic()?.vehicles.length ?? 0) > 0;
    },
    undefined,
    { timeout: 60_000 },
  );
const settle = (frames = 4) =>
  p().evaluate(
    (n) =>
      new Promise<void>((resolve) => {
        let left = n;
        const tick = () => (--left <= 0 ? resolve() : requestAnimationFrame(tick));
        requestAnimationFrame(tick);
      }),
    frames,
  );
const shot = (name: string) =>
  SHOTS ? p().screenshot({ path: path.join(SHOTS, `${name}.png`) }) : Promise.resolve(null);

async function goToDistrict(label: string) {
  if (!(await p().$('[data-testid="world-district-list"]')))
    await p().click('[data-testid="world-view-districts"]');
  await p().click(`button[data-district="${label}"]`);
  await p().click('[data-testid="world-view-districts"]');
  await settle(6);
}
async function openCity() {
  const open = await p().$('[data-testid="city-controls"]');
  if (!open) await p().click('[data-testid="city-button"]');
  await p().waitForSelector('[data-testid="city-controls"]');
}
async function choose(testid: string) {
  await p().focus(`[data-testid="${testid}"]`);
  await p().keyboard.press('Enter');
  await settle(6);
}
/**
 * A sky with no weather over the street. These scenarios find the vehicles by matching their real sprite files pixel for pixel, which a
 * weather veil or a falling particle over them would defeat. The default is now Automatic regional weather (the seeded start has rain
 * over the City, where the Metropolis is), so the premise is made explicit here: choose Clear and let its four-second fade finish
 * (reduced motion swaps at once). The panel is left as it was found, so nothing covers the street.
 */
async function clearSky(): Promise<void> {
  const wasOpen = (await p().$('[data-testid="city-controls"]')) !== null;
  await openCity();
  await choose('city-weather-clear');
  await p().waitForTimeout(4600);
  if (!wasOpen) await p().click('[data-testid="city-button"]');
  await settle(6);
}
const pressed = (testid: string) =>
  p().$eval(`[data-testid="${testid}"]`, (el) => el.getAttribute('aria-pressed'));

// ── screen calibration, from the real mouse ───────────────────────────────────

interface Calibration {
  /** Tile size in canvas pixels, and the offset of tile (0,0)'s top-left from the canvas top-left. */
  size: number;
  ox: number;
  oy: number;
  left: number;
  top: number;
  width: number;
  height: number;
}
/** The tile under a page point, as the App's own hover reports it. */
async function tileUnder(x: number, y: number): Promise<{ col: number; row: number } | null> {
  await p().mouse.move(x, y);
  return p().evaluate(() => {
    const o = (
      window as unknown as {
        __diHarnessOffice: { hoveredTile: { col: number; row: number } | null };
      }
    ).__diHarnessOffice;
    return o.hoveredTile ? { col: o.hoveredTile.col, row: o.hoveredTile.row } : null;
  });
}
/** The first page coordinate (scanning `axis` from `from`) where the hovered tile index increments. */
async function boundary(
  axis: 'x' | 'y',
  from: number,
  fixed: number,
): Promise<{ at: number; tile: number }> {
  const read = async (v: number) => {
    const t = await tileUnder(axis === 'x' ? v : fixed, axis === 'x' ? fixed : v);
    assert.ok(t, 'the pointer must be over the world');
    return axis === 'x' ? t.col : t.row;
  };
  const start = await read(from);
  let lo = from;
  let hi = from;
  for (let step = 1; step <= 120; step++) {
    hi = from + step * 4;
    if ((await read(hi)) !== start) break;
    lo = hi;
  }
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    if ((await read(mid)) === start) lo = mid;
    else hi = mid;
  }
  return { at: hi, tile: start + 1 };
}
async function calibrate(): Promise<Calibration> {
  const rect = await p().$eval('canvas', (c) => {
    const r = (c as HTMLCanvasElement).getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  });
  // clear of the left-hand panels and the right-hand office panel, so the canvas receives the pointer
  const x0 = rect.left + 600;
  const y0 = rect.top + 400;
  const bx1 = await boundary('x', x0, y0);
  const bx2 = await boundary('x', bx1.at + 2, y0);
  const by1 = await boundary('y', y0, x0);
  const by2 = await boundary('y', by1.at + 2, x0);
  const size = bx2.at - bx1.at;
  assert.equal(by2.at - by1.at, size, 'tiles are square');
  return {
    size,
    ox: bx1.at - rect.left - bx1.tile * size,
    oy: by1.at - rect.top - by1.tile * size,
    ...rect,
  };
}

// ── template matching against the real art ────────────────────────────────────

const spriteCache = new Map<string, { w: number; h: number; px: Buffer } | null>();
function loadSprite(type: string) {
  if (spriteCache.has(type)) return spriteCache.get(type)!;
  const file = path.join(FURNITURE, type, `${type}.png`);
  const out = fs.existsSync(file)
    ? (() => {
        const png = PNG.sync.read(fs.readFileSync(file));
        return { w: png.width, h: png.height, px: png.data };
      })()
    : null;
  spriteCache.set(type, out);
  return out;
}
const FACING = ['RIGHT', 'DOWN', 'LEFT', 'UP'];

interface Seen {
  id: string;
  kind: string;
  heading: number;
  hx: number;
  hy: number;
  emergency: boolean;
  /** The canvas pixels at the predicted rectangle (RGBA), or null when off canvas. */
  crop: number[] | null;
  w: number;
  h: number;
  x: number;
  y: number;
}

/** Atomically (one evaluate, no frame between) read every driving vehicle's pose and the canvas pixels where its sprite should be. */
async function capture(
  cal: Calibration,
  dims: Record<string, { w: number; h: number }>,
): Promise<Seen[]> {
  return p().evaluate(
    ({ cal, dims, facing }) => {
      const o = (
        window as unknown as {
          __diHarnessOffice: {
            getTraffic(): {
              visible(): Array<{
                id: string;
                kind: string;
                heading: number;
                hx: number;
                hy: number;
                fade: number;
                emergency: boolean;
                state: string;
              }>;
            };
          };
        }
      ).__diHarnessOffice;
      const canvas = document.querySelector('canvas') as HTMLCanvasElement;
      const g = canvas.getContext('2d')!;
      const zoom = cal.size / 16;
      // typed as Seen: every pushed object is checked against it field by field, so no cast is needed
      const out: Seen[] = [];
      for (const v of o.getTraffic().visible()) {
        if (v.fade < 1) continue;
        const d = dims[`VEHICLE_${v.kind}_${facing[v.heading]}`];
        if (!d) continue;
        const bottom = (v.hy + 1) * 16;
        const wx =
          v.heading === 0
            ? (v.hx + 1) * 16 - d.w
            : v.heading === 2
              ? v.hx * 16
              : v.hx * 16 + 8 - d.w / 2;
        const wy = bottom - d.h;
        const x = Math.round(cal.ox + wx * zoom);
        const y = Math.round(cal.oy + wy * zoom);
        const w = d.w * zoom;
        const h = d.h * zoom;
        let crop: number[] | null = null;
        if (x >= 0 && y >= 0 && x + w <= canvas.width && y + h <= canvas.height)
          crop = Array.from(g.getImageData(x, y, w, h).data);
        out.push({
          id: v.id,
          kind: v.kind,
          heading: v.heading,
          hx: v.hx,
          hy: v.hy,
          emergency: v.emergency,
          crop,
          w,
          h,
          x,
          y,
        });
      }
      return out;
    },
    { cal, dims, facing: FACING },
  );
}

/** How much of a sprite's opaque art is found, exactly, at its predicted place (0..1). */
function matchOf(seen: Seen, zoom: number): number {
  const sprite = loadSprite(`VEHICLE_${seen.kind}_${FACING[seen.heading]}`);
  if (!sprite || !seen.crop) return 0;
  let opaque = 0;
  let same = 0;
  for (let sy = 0; sy < sprite.h; sy++) {
    for (let sx = 0; sx < sprite.w; sx++) {
      const a = sprite.px[(sy * sprite.w + sx) * 4 + 3];
      if (a !== 255) continue;
      opaque++;
      // the centre pixel of the zoom x zoom block this sprite pixel became
      const cx = Math.floor((sx + 0.5) * zoom);
      const cy = Math.floor((sy + 0.5) * zoom);
      const i = (cy * seen.w + cx) * 4;
      if (
        seen.crop[i] === sprite.px[(sy * sprite.w + sx) * 4] &&
        seen.crop[i + 1] === sprite.px[(sy * sprite.w + sx) * 4 + 1] &&
        seen.crop[i + 2] === sprite.px[(sy * sprite.w + sx) * 4 + 2]
      )
        same++;
    }
  }
  return opaque === 0 ? 0 : same / opaque;
}

function spriteDims(): Record<string, { w: number; h: number }> {
  const dims: Record<string, { w: number; h: number }> = {};
  for (const d of fs.readdirSync(FURNITURE)) {
    if (!d.startsWith('VEHICLE_')) continue;
    const m = JSON.parse(fs.readFileSync(path.join(FURNITURE, d, 'manifest.json'), 'utf-8')) as {
      width: number;
      height: number;
    };
    dims[d] = { w: m.width, h: m.height };
  }
  return dims;
}

const trafficOf = <T>(
  fn: (t: {
    vehicles: Array<Record<string, unknown>>;
    counters: Record<string, number>;
    hiddenUids(): Set<string>;
    visible(): Array<Record<string, unknown>>;
  }) => T,
) =>
  p().evaluate((src) => {
    const o = (window as unknown as { __diHarnessOffice: { getTraffic(): never } })
      .__diHarnessOffice;
    return new Function('t', `return (${src})(t)`)(o.getTraffic());
  }, fn.toString()) as Promise<T>;

// ── the tests ─────────────────────────────────────────────────────────────────

test(
  "REAL MOTION: the world's own vehicle art is found on the canvas where each pose says, and found somewhere else a moment later",
  async () => {
    await boot();
    await clearSky();
    const dims = spriteDims();
    await waitForDriving();
    let best: { district: string; cal: Calibration; first: Seen[] } | null = null;
    for (const district of ['Metropolis']) {
      await goToDistrict(district);
      const cal = await calibrate();
      const zoom = cal.size / 16;
      const first = (await capture(cal, dims)).filter((s) => matchOf(s, zoom) >= 0.95);
      if (!best || first.length > best.first.length) best = { district, cal, first };
      if (first.length >= 6) break;
    }
    const { district, cal, first } = best!;
    const zoom = cal.size / 16;
    assert.equal(cal.size % 16, 0, 'a pixel-perfect integer zoom');
    assert.ok(
      first.length >= 4,
      `only ${first.length} vehicles were found drawn in view at ${district}`,
    );
    await shot('living-city-traffic-a');
    // a moment later: the same vehicles, found again, somewhere else
    await p().waitForTimeout(700);
    const second = await capture(cal, dims);
    const secondById = new Map(second.map((s) => [s.id, s]));
    let moved = 0;
    let foundAgain = 0;
    for (const a of first) {
      const b = secondById.get(a.id);
      if (!b || matchOf(b, zoom) < 0.95) continue;
      foundAgain++;
      if (Math.hypot(b.hx - a.hx, b.hy - a.hy) >= 0.75) moved++;
    }
    assert.ok(foundAgain >= 3, `only ${foundAgain} of ${first.length} were found drawn again`);
    assert.ok(
      moved >= 2,
      `only ${moved} of them were found drawn at a different place (of ${foundAgain})`,
    );
    // and the world's counters agree that it is not a still picture
    const counters = await trafficOf((t) => ({ ...t.counters }));
    assert.ok(counters.tilesDriven > 20, `${counters.tilesDriven} tiles driven`);
    assert.ok(counters.stops >= 1, 'something has stopped at a line and gone on');
    console.log(
      `MEASURED traffic motion at ${district}: ${first.length} vehicles matched on canvas, ${foundAgain} found again, ${moved} moved >= 0.75 tile in 0.7 s; counters ${JSON.stringify(counters)}`,
    );
  },
  STEPS_MS,
);

test(
  'the Traffic control: Off hands every vehicle back to its parked furniture, Low and Normal drive, and the choice is saved and survives a reload',
  async () => {
    await boot();
    await waitForBuilt();
    await openCity();
    assert.equal(await pressed('city-traffic-normal'), 'true');
    assert.match(
      await p().$eval('[data-testid="city-traffic-status"]', (e) => (e as HTMLElement).innerText),
      /stop at crossings and give way to people/,
    );
    assert.match(
      await p().$eval('[data-testid="city-traffic-note"]', (e) => (e as HTMLElement).innerText),
      /none has any priority or meaning/,
    );
    const furnitureCount = () =>
      p().evaluate(
        () =>
          (window as unknown as { __diHarnessOffice: { furniture: unknown[] } }).__diHarnessOffice
            .furniture.length,
      );
    const driving = await furnitureCount();
    await choose('city-traffic-off');
    assert.equal(await pressed('city-traffic-off'), 'true');
    const parked = await furnitureCount();
    assert.equal(parked - driving, 68, 'off puts back exactly the 68 static copies');
    assert.equal(await trafficOf((t) => t.visible().length), 0);
    await choose('city-traffic-low');
    assert.equal(await trafficOf((t) => t.hiddenUids().size), 24);
    await choose('city-traffic-normal');
    assert.equal(await trafficOf((t) => t.hiddenUids().size), 68);
    // saved through the closed message: the exact four keys, nothing else
    assert.deepEqual(
      saves.map((m) => (m.state as { traffic: string }).traffic),
      ['off', 'low', 'normal'],
    );
    await choose('city-traffic-off');
    // the saved record is the strict v2 record (item 18): the same lighting and traffic choice, and the season and weather state beside it
    const saved = readCityPresentation() as unknown as {
      v: number;
      lighting: string;
      traffic: string;
      season: { mode: string; season: string };
      weather: { mode: string };
    };
    assert.deepEqual(Object.keys(saved).sort(), ['lighting', 'season', 'traffic', 'v', 'weather']);
    assert.deepEqual(
      [saved.v, saved.lighting, saved.traffic, saved.season.mode, saved.season.season, saved.weather.mode],
      [2, 'day', 'off', 'auto', 'summer', 'auto'],
    );
    await boot();
    await openCity();
    assert.equal(await pressed('city-traffic-off'), 'true');
    // a saved Off never even builds the traffic, so nothing is hidden and every static copy is drawn
    const hiddenAfterReload = await p().evaluate(
      () =>
        (
          window as unknown as {
            __diHarnessOffice: { getTraffic(): { hiddenUids(): Set<string> } | null };
          }
        ).__diHarnessOffice
          .getTraffic()
          ?.hiddenUids().size ?? 0,
    );
    assert.equal(hiddenAfterReload, 0, 'a saved off is applied before anything is drawn');
    assert.equal(saves.length, 4, 'loading a saved choice does not save it again');
  },
  STEPS_MS,
);

test(
  'THE TRAFFIC CHOICE round-trips EXACTLY through the v2 record, and a saved v1 record carrying that choice migrates to the SAME level, for every level',
  async () => {
    const stateDir = path.join(sandbox, 'di-root');
    const file = path.join(stateDir, CITY_PRESENTATION_FILE_NAME);
    // the traffic engine's own count of hidden static copies, the values the Traffic control test above pins: a saved Off never builds the
    // traffic (0), Low hides 24 copies (TRAFFIC_CAP_LOW), Normal hides 68
    const hidden = () =>
      p().evaluate(
        () =>
          (
            window as unknown as {
              __diHarnessOffice: { getTraffic(): { hiddenUids(): Set<string> } | null };
            }
          ).__diHarnessOffice.getTraffic()?.hiddenUids().size ?? 0,
      );
    const expectedHidden = { off: 0, low: 24, normal: 68 } as const;
    for (const level of ['off', 'low', 'normal'] as const) {
      // (a) a saved v1 record with this traffic choice: migrated on load to the same level; reading writes nothing
      const old = JSON.stringify({ v: 1, lighting: 'day', weather: 'off', traffic: level });
      fs.mkdirSync(stateDir, { recursive: true });
      fs.writeFileSync(file, old);
      saves = [];
      await boot();
      await openCity();
      for (const other of ['off', 'low', 'normal'])
        assert.equal(await pressed(`city-traffic-${other}`), String(other === level), `v1 ${level}: ${other}`);
      assert.equal(await hidden(), expectedHidden[level], `v1 ${level}: the traffic engine got the same level`);
      assert.equal(fs.readFileSync(file, 'utf-8'), old, 'migration is a read: the v1 file is untouched');
      assert.equal(saves.length, 0);
      // (b) the v2 round trip: choose another level, then this one; the saved record holds exactly this level, and a reload restores it
      const other = level === 'off' ? 'low' : 'off';
      await choose(`city-traffic-${other}`);
      await choose(`city-traffic-${level}`);
      const record = readCityPresentation() as unknown as { v: number; traffic: string };
      assert.deepEqual([record.v, record.traffic], [2, level]);
      assert.deepEqual(
        saves.map((m) => (m.state as { traffic: string }).traffic),
        [other, level],
        'each choice was saved, with its exact level',
      );
      await boot();
      await openCity();
      for (const o of ['off', 'low', 'normal'])
        assert.equal(await pressed(`city-traffic-${o}`), String(o === level), `v2 ${level}: ${o}`);
      assert.equal(await hidden(), expectedHidden[level], `v2 ${level}: the traffic engine got the same level`);
    }
  },
  STEPS_MS,
);

test(
  'REAL AGENT PRIORITY in the app: a real agent put on a road ahead of a driving vehicle holds it back, and is never held back by it',
  async () => {
    await boot();
    await goToDistrict('Metropolis');
    await waitForDriving();
    const blockedBefore = await p().evaluate(() =>
      [
        ...(window as unknown as { __diHarnessOffice: { blockedTiles: Set<string> } })
          .__diHarnessOffice.blockedTiles,
      ]
        .sort()
        .join('|'),
    );
    // Stand the fixture's real agent on a tile a driving vehicle has ALREADY reserved (within one
    // tile of that claim, not on a tile it is standing in). releaseHaloClaims gives the claim back
    // on the next traffic update. Count only after that one update: the original tolerance is still
    // exactly zero, and the look before the update must already see the reserved tile.
    const placed = await p().evaluate(() => {
      const o = (
        window as unknown as {
          __diHarnessOffice: {
            update(dt: number): void;
            getTraffic(): {
              vehicles: Array<{
                state: string;
                v: number;
                route: number[];
                head: number;
                claimed: number;
              }>;
            };
            layout: { cols: number; rows: number };
            characters: Map<
              number,
              { tileCol: number; tileRow: number; x: number; y: number; path: unknown[] }
            >;
          };
        }
      ).__diHarnessOffice;
      const cols = o.layout.cols;
      const rows = o.layout.rows;
      const traffic = o.getTraffic();
      const absent = (state: string) => state === 'inactive' || state === 'gone' || state === 'out';
      const onRoad = (state: string) =>
        state === 'in' || state === 'drive' || state === 'stop' || state === 'end';
      // Same window the assertion uses: from four tiles behind the body through the last claim.
      const look = (col: number, row: number, claimedOf: (index: number) => number) => {
        let hits = 0;
        let forward = 0;
        let stoodOn = false;
        traffic.vehicles.forEach((w, index) => {
          if (absent(w.state)) return;
          const body = Math.floor(w.head);
          const claimed = claimedOf(index);
          for (let k = Math.max(0, body - 4); k <= claimed && k < w.route.length; k++) {
            const tt = w.route[k];
            const dist = Math.max(
              Math.abs((tt % cols) - col),
              Math.abs(Math.floor(tt / cols) - row),
            );
            if (dist === 0) stoodOn = true;
            if (dist <= 1) {
              hits++;
              if (k > body) forward++;
            }
          }
        });
        return { hits, forward, stoodOn };
      };
      // What releaseHaloClaims would leave: claims ahead of the body that sit in the agent's halo
      // are given back from the front, and everything else stays.
      const afterRelease = (col: number, row: number) => {
        const claimed = traffic.vehicles.map((w) => w.claimed);
        traffic.vehicles.forEach((w, index) => {
          if (!onRoad(w.state)) return;
          const body = Math.floor(w.head);
          while (claimed[index] > body && claimed[index] < w.route.length) {
            const tt = w.route[claimed[index]];
            if (Math.max(Math.abs((tt % cols) - col), Math.abs(Math.floor(tt / cols) - row)) > 1)
              break;
            claimed[index]--;
          }
        });
        return look(col, row, (index) => claimed[index]).hits;
      };
      let spot: { col: number; row: number; before: number } | null = null;
      for (const v of traffic.vehicles) {
        if (v.state !== 'drive' || v.v <= 2 || v.claimed <= Math.floor(v.head)) continue;
        const tip = v.route[v.claimed];
        const tipCol = tip % cols;
        const tipRow = Math.floor(tip / cols);
        for (let dy = -1; dy <= 1 && !spot; dy++) {
          for (let dx = -1; dx <= 1 && !spot; dx++) {
            if (dx === 0 && dy === 0) continue;
            const col = tipCol + dx;
            const row = tipRow + dy;
            if (col < 0 || row < 0 || col >= cols || row >= rows) continue;
            const seen = look(col, row, (index) => traffic.vehicles[index].claimed);
            // Beside a claim the vehicle already holds ahead of its body, and not standing in
            // one (that is the yield, not the claim it must give back). One update must be able
            // to clear every tile the assertion counts.
            if (seen.hits > 0 && seen.forward > 0 && !seen.stoodOn && afterRelease(col, row) === 0)
              spot = { col, row, before: seen.hits };
          }
        }
        if (spot) break;
      }
      if (!spot) return null;
      const { col, row, before } = spot;
      const agent = o.characters.get(9)!;
      const hold = () => {
        agent.tileCol = col;
        agent.tileRow = row;
        agent.x = col * 16 + 8;
        agent.y = row * 16 + 8;
        agent.path = [];
      };
      hold();
      // The one tick the vehicle is allowed. Counting starts at the state this update leaves.
      o.update(1 / 60);
      const log = { violations: 0, frames: 0 };
      const loop = () => {
        hold();
        log.frames++;
        const t = o.getTraffic() as unknown as {
          vehicles: Array<{ state: string; route: number[]; head: number; claimed: number }>;
        };
        for (const w of t.vehicles) {
          if (absent(w.state)) continue;
          for (
            let k = Math.max(0, Math.floor(w.head) - 4);
            k <= w.claimed && k < w.route.length;
            k++
          ) {
            const tt = w.route[k];
            if (Math.abs((tt % cols) - col) <= 1 && Math.abs(Math.floor(tt / cols) - row) <= 1)
              log.violations++;
          }
        }
        requestAnimationFrame(loop);
      };
      (window as unknown as { __priorityLog: typeof log }).__priorityLog = log;
      loop();
      return { col, row, before };
    });
    assert.ok(placed, 'a driving vehicle should already be holding a tile next to the agent');
    assert.ok(
      placed.before > 0,
      'the agent was not within one tile of a claim the vehicle already held',
    );
    await p().waitForTimeout(4000);
    const log = await p().evaluate(
      () =>
        (window as unknown as { __priorityLog: { violations: number; frames: number } })
          .__priorityLog,
    );
    assert.ok(log.frames > 60, `${log.frames} frames observed`);
    assert.equal(log.violations, 0, 'a vehicle claimed a tile within one of a real agent');
    const denials = await trafficOf((t) => t.counters.pedestrianDenials);
    assert.ok(denials > 0, 'the agent must actually have held a vehicle back');
    const blockedAfter = await p().evaluate(() =>
      [
        ...(window as unknown as { __diHarnessOffice: { blockedTiles: Set<string> } })
          .__diHarnessOffice.blockedTiles,
      ]
        .sort()
        .join('|'),
    );
    assert.equal(
      blockedAfter,
      blockedBefore,
      'decoration never changes what a real agent can walk on',
    );
    await shot('living-city-traffic-agent-priority');
  },
  STEPS_MS,
);

test(
  'HOVER: pointing at a vehicle hits that vehicle and draws its "decorative" label above it',
  async () => {
    await boot();
    await clearSky();
    await goToDistrict('Metropolis');
    await waitForDriving();
    const cal = await calibrate();
    const dims = spriteDims();
    const zoom = cal.size / 16;
    // freeze time so the vehicle stays where we aim: reduced motion holds every vehicle still
    await p().emulateMedia({ reducedMotion: 'reduce' });
    await p().waitForTimeout(400);
    const seen = (await capture(cal, dims)).filter((s) => matchOf(s, zoom) >= 0.95 && s.crop);
    assert.ok(seen.length >= 3, `${seen.length} vehicles in view`);
    const aim = seen[0];
    await p().mouse.move(cal.left + aim.x + aim.w / 2, cal.top + aim.y + aim.h * 0.65);
    await settle(3);
    const hovered = await p().evaluate(
      () =>
        (window as unknown as { __diHarnessOffice: { hoveredVehicleId: string | null } })
          .__diHarnessOffice.hoveredVehicleId,
    );
    assert.equal(hovered, aim.id, 'the pointer over a vehicle hits that vehicle');
    // the label is drawn on the canvas above it: pixels change there between hovering and not
    const labelBox = {
      x: Math.round(aim.x + aim.w / 2 - 70),
      y: Math.round(aim.y - 26),
      w: 140,
      h: 24,
    };
    const region = () =>
      p().evaluate(
        (b) =>
          Array.from(
            (document.querySelector('canvas') as HTMLCanvasElement)
              .getContext('2d')!
              .getImageData(b.x, b.y, b.w, b.h).data,
          ),
        labelBox,
      );
    const withLabel = await region();
    await shot('living-city-traffic-hover'); // while the pointer is still on the vehicle
    await p().mouse.move(cal.left + 5, cal.top + 5);
    await settle(3);
    const without = await region();
    let changed = 0;
    for (let i = 0; i < withLabel.length; i += 4)
      if (
        withLabel[i] !== without[i] ||
        withLabel[i + 1] !== without[i + 1] ||
        withLabel[i + 2] !== without[i + 2]
      )
        changed++;
    assert.ok(changed > 300, `the label changed only ${changed} pixels`);
  },
  STEPS_MS,
);

test(
  'REDUCED MOTION in the app: the street at rest looks like the parked street it came from, held still',
  async () => {
    await boot('reduce');
    await waitForBuilt();
    await clearSky();
    await goToDistrict('Metropolis');
    await openCity();
    const sig = () =>
      p().evaluate(() => {
        const c = document.querySelector('canvas') as HTMLCanvasElement;
        return Array.from(c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data);
      });
    // the comparison below is only worth anything with vehicles actually in view: find them on the real canvas
    const cal = await calibrate();
    const inView = (await capture(cal, spriteDims())).filter(
      (x) => matchOf(x, cal.size / 16) >= 0.95,
    );
    assert.ok(
      inView.length >= 4,
      `only ${inView.length} vehicles are in view: the comparison would be vacuous`,
    );
    const moving = await sig();
    // held still: a second later it is the same picture
    await p().waitForTimeout(900);
    const later = await sig();
    let drift = 0;
    for (let i = 0; i < moving.length; i += 4)
      if (
        moving[i] !== later[i] ||
        moving[i + 1] !== later[i + 1] ||
        moving[i + 2] !== later[i + 2]
      )
        drift++;
    assert.equal(drift, 0, `${drift} pixels changed in a still frame`);
    assert.equal(await trafficOf((t) => t.counters.tilesDriven), 0);
    // off: the parked furniture is drawn instead. The two pictures are the same street
    await choose('city-traffic-off');
    const parked = await sig();
    let differ = 0;
    for (let i = 0; i < moving.length; i += 4)
      if (
        moving[i] !== parked[i] ||
        moving[i + 1] !== parked[i + 1] ||
        moving[i + 2] !== parked[i + 2]
      )
        differ++;
    const fraction = differ / (moving.length / 4);
    console.log(
      `MEASURED: with ${inView.length} vehicles in view, traffic at rest vs parked furniture differ in ${differ} of ${moving.length / 4} pixels (${(fraction * 100).toFixed(3)}%)`,
    );
    assert.ok(
      fraction < 0.01,
      `${(fraction * 100).toFixed(2)}% of pixels differ between the street at rest and the parked street`,
    );
    await shot('living-city-traffic-reduced-motion');
  },
  STEPS_MS,
);

test(
  'SAFE RESTART: a reload brings the same vehicles back, once each, at the places they were parked',
  async () => {
    await boot('reduce');
    await waitForBuilt();
    const poses = () => trafficOf((t) => t.vehicles.map((v) => [v.id, v.hx, v.hy, v.state]));
    const first = await poses();
    await boot('reduce');
    await waitForBuilt();
    const second = await poses();
    assert.equal(new Set(second.map((r) => r[0])).size, second.length, 'no duplicate vehicle');
    assert.equal(second.length, 68);
    assert.deepEqual(
      second.map((r) => r[0]),
      first.map((r) => r[0]),
    );
    // under reduced motion every vehicle is shown at its parked place at once: the same places both times
    const shown = (rows: Array<Array<unknown>>) =>
      rows.filter((r) => r[3] === 'drive').map((r) => `${r[0]}@${r[1]},${r[2]}`);
    assert.deepEqual(shown(second), shown(first));
    assert.ok(shown(second).length >= 60);
  },
  STEPS_MS,
);

/** How much of a sprite's opaque art is found at its predicted place AFTER the rain veil has tinted it: each channel within `tol` of
 *  sprite * (1 - a) + veil * a (0..1). The exact matcher above asks for the untinted sprite; this asks for the tinted one. */
function tintMatchOf(seen: Seen, zoom: number, tol: number): number {
  const sprite = loadSprite(`VEHICLE_${seen.kind}_${FACING[seen.heading]}`);
  if (!sprite || !seen.crop) return 0;
  const { r, g, b, a } = CITY_OVERCAST_RAIN;
  const veil = [r, g, b];
  let opaque = 0;
  let near = 0;
  for (let sy = 0; sy < sprite.h; sy++) {
    for (let sx = 0; sx < sprite.w; sx++) {
      const k = (sy * sprite.w + sx) * 4;
      if (sprite.px[k + 3] !== 255) continue;
      opaque++;
      const i = (Math.floor((sy + 0.5) * zoom) * seen.w + Math.floor((sx + 0.5) * zoom)) * 4;
      let ok = true;
      for (let c = 0; c < 3; c++) {
        const expected = sprite.px[k + c] * (1 - a) + veil[c] * a;
        if (Math.abs(seen.crop[i + c] - expected) > tol) ok = false;
      }
      if (ok) near++;
    }
  }
  return opaque === 0 ? 0 : near / opaque;
}

test(
  'UNDER AUTOMATIC REGIONAL WEATHER (the default: rain over the Metropolis, with its veil) the vehicles are still DRAWN at their poses, tinted by the veil, and still hit-testable',
  async () => {
    await boot(); // the default: Automatic regional weather. NO Clear premise here: this is the combined-effect check
    await waitForDriving();
    await goToDistrict('Metropolis');
    const cal = await calibrate();
    const dims = spriteDims();
    const zoom = cal.size / 16;
    // hold everything still: reduced motion holds every vehicle and every weather particle on one frame
    await p().emulateMedia({ reducedMotion: 'reduce' });
    await p().waitForTimeout(600);
    interface Probe {
      cityKind: string;
      rainAlpha: number;
      renderInputWeather: boolean;
      renderInputSeason: boolean;
    }
    const probe = await p().evaluate((): Probe => {
      const o = (
        window as unknown as {
          __diHarnessOffice: {
            city: { regional: { kindAt(slot: number): string; alpha(k: string, slot: number): number } };
            cityRenderInput(): { weather: object | null; season: object | null } | null;
          };
        }
      ).__diHarnessOffice;
      const input = o.cityRenderInput();
      return {
        cityKind: o.city.regional.kindAt(0),
        rainAlpha: o.city.regional.alpha('rain', 0),
        renderInputWeather: input?.weather != null,
        renderInputSeason: input?.season != null,
      };
    });
    // the premise, measured: the City region (where the Metropolis is) is in rain at full strength, so its veil is laid
    assert.deepEqual(probe, {
      cityKind: 'rain',
      rainAlpha: 1,
      renderInputWeather: true,
      renderInputSeason: false,
    });
    const seen = (await capture(cal, dims)).filter((s) => s.crop);
    assert.ok(seen.length >= 4, `${seen.length} vehicles in view`);
    const exact = seen.map((s) => matchOf(s, zoom));
    const tinted = seen.map((s) => tintMatchOf(s, zoom, 8));
    console.log(
      `MEASURED [vehicles under the default Automatic rain veil] ${seen.length} vehicles in view; exact (untinted) match ${JSON.stringify(exact.map((v) => Math.round(v * 100) / 100))}; veil-tinted match (tolerance 8/255) ${JSON.stringify(tinted.map((v) => Math.round(v * 100) / 100))}`,
    );
    // DRAWN: the vehicle's own art is on the canvas at its pose, tinted by the veil (about 2% of any crop is a rain streak)
    const drawn = tinted.filter((v) => v >= 0.9).length;
    assert.ok(drawn >= Math.ceil(seen.length * 0.75), `${drawn} of ${seen.length} vehicles are found, veil-tinted, at their poses`);
    // and the veil really is what separates the two matchers: it is a tint, not a loss of the vehicle
    assert.ok(Math.max(...exact) < 0.95, 'the veil tint is what the exact (untinted) matcher cannot see through');
    // HIT-TESTABLE: pointing at the centre of each drawn vehicle's rectangle finds that vehicle (pose based, not pixel based)
    for (const s of seen.filter((_, i) => tinted[i] >= 0.9)) {
      const wx = (s.x + s.w / 2 - cal.ox) / zoom;
      const wy = (s.y + s.h / 2 - cal.oy) / zoom;
      const hit = await p().evaluate(
        ([x, y]) =>
          (
            window as unknown as {
              __diHarnessOffice: { getVehicleAt(x: number, y: number): { id: string } | null };
            }
          ).__diHarnessOffice.getVehicleAt(x, y)?.id ?? null,
        [wx, wy],
      );
      assert.equal(hit, s.id, `pointing at ${s.id} finds it`);
    }
    await shot('living-city-traffic-under-automatic-rain');
  },
  STEPS_MS,
);
