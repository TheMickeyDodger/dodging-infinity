/**
 * LIVING CITY 6–9, lighting and weather through the ACTUAL App, in a real browser, over the SHIPPED
 * living metropolis, with the REAL server persistence under an owned temp root.
 *
 * WHAT THIS EVIDENCE IS, EXACTLY: real-App INTEGRATION OVER SOURCE. The App is mounted from source by
 * Vite's dev server (`test/harness/di-app.html` -> `src/main.tsx`); it is NOT the production package and
 * this file is NOT production-package browser proof. The office state the agents come from is a labelled
 * FIXTURE (`di-office-state-transitions-v2.FIXTURE.json`); nothing here is evidence of a live provider or
 * Herdr. The preference store is the real `cityPresentationPersistence` under a temp root this test owns:
 * no personal profile, layout or launcher is read or written.
 *
 * What is REAL: the App, the City button and panel, the keyboard path, the OfficeState and its lighting and
 * weather, the canvas, the WebSocket protocol, the server's `handleDiClientMessage` and the persistence
 * module. What is CONTROLLED: the office state (fixture) and the WebSocket peer (this test).
 *
 * Pixels are compared under REDUCED MOTION (emulated), where the scene is one still frame: residents,
 * trains and weather hold still and the lighting jumps to its stage, so two reads of the same state are
 * identical and a difference is the city layer and nothing else.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with
 * neither, every test is skipped with an explicit message.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
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
import { AgentStateStore } from '../../server/src/agentStateStore.ts';
import { CITY_PRESENTATION_FILE_NAME } from '../../server/src/constants.ts';
import { readCityPresentation } from '../../server/src/di/cityPresentationPersistence.ts';
import { handleDiClientMessage } from '../../server/src/di/diClientMessages.ts';
import { resetStateRootForTests, setStateRoot } from '../../server/src/stateRoot.ts';

const STEPS_MS = 120_000;
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
let stateRoot = '';
/** Every message the page sent that this test looks at, in order. */
let saves: Array<Record<string, unknown>> = [];

const ctxBase = { store: new AgentStateStore(), cache: {} as never, extensionVersion: 'test' };

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    const send = (m: Record<string, unknown>) =>
      ws.readyState === ws.OPEN && ws.send(JSON.stringify(m));
    if (msg.type === 'webviewReady') {
      // what the real handshake does for this record: the saved choice (or null), read by the real module
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
    console.warn(`SKIPPED (living city app): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  // an owned, empty preference store for every test
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true });
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-living-city-')));
  stateRoot = path.join(sandbox, 'di-root');
  resetStateRootForTests();
  setStateRoot(stateRoot);
  saves = [];
}, STEPS_MS);

type Reduced = 'reduce' | 'no-preference';
/** Open the App, hand it the shipped world and the fixture's agents, and wait until it is drawn. */
async function boot(reducedMotion: Reduced = 'reduce'): Promise<Page> {
  await page?.close();
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.emulateMedia({ reducedMotion });
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
  await settle();
  return page;
}
const p = () => page!;
/** A few animation frames, so the canvas shows the current state. */
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

interface Metrics {
  mean: number;
  /** The brightness at the 99th percentile: what stays lit. */
  p99: number;
  /** A fingerprint of the sampled pixels: equal states have equal fingerprints. */
  sig: string;
  w: number;
  h: number;
}
const metrics = (): Promise<Metrics> =>
  p().evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement;
    const g = canvas.getContext('2d')!;
    const { width: w, height: h } = canvas;
    const d = g.getImageData(0, 0, w, h).data;
    const hist = new Uint32Array(256);
    let sum = 0;
    let n = 0;
    let sig = 0x811c9dc5;
    for (let i = 0; i < d.length; i += 4 * 7) {
      const lum = Math.round(0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]);
      hist[lum]++;
      sum += lum;
      n++;
      sig = Math.imul(sig ^ d[i] ^ (d[i + 1] << 8) ^ (d[i + 2] << 16), 0x01000193) >>> 0;
    }
    let seen = 0;
    let p99 = 255;
    for (let v = 0; v < 256; v++) {
      seen += hist[v];
      if (seen >= n * 0.99) {
        p99 = v;
        break;
      }
    }
    return { mean: sum / n, p99, sig: sig.toString(16), w, h };
  });
const shot = (name: string) =>
  SHOTS ? p().screenshot({ path: path.join(SHOTS, `${name}.png`) }) : Promise.resolve(null);

async function openCity() {
  await p().waitForSelector('[data-testid="city-button"]', { timeout: 30_000 });
  const open = await p().$('[data-testid="city-controls"]');
  if (!open) await p().click('[data-testid="city-button"]');
  await p().waitForSelector('[data-testid="city-controls"]');
}
/** Choose a control the way a keyboard user does: focus it, press Enter. */
async function choose(testid: string) {
  await p().focus(`[data-testid="${testid}"]`);
  await p().keyboard.press('Enter');
  await settle(6);
}
/** Camera travel to a named district, the way the Districts list does it (view only). */
async function goToDistrict(label: string) {
  if (!(await p().$('[data-testid="world-district-list"]')))
    await p().click('[data-testid="world-view-districts"]');
  await p().click(`button[data-district="${label}"]`);
  await p().click('[data-testid="world-view-districts"]'); // close the list again
  await settle(6);
}
const pressed = (testid: string) =>
  p().$eval(`[data-testid="${testid}"]`, (el) => el.getAttribute('aria-pressed'));
const text = (testid: string) =>
  p().$eval(`[data-testid="${testid}"]`, (el) => (el as HTMLElement).innerText);

test(
  'the City panel opens from the toolbar, says what it is, and starts as the city always looked',
  async () => {
    await boot();
    await openCity();
    assert.equal(
      await p().$eval('[data-testid="city-button"]', (el) => el.getAttribute('aria-expanded')),
      'true',
    );
    assert.match(await text('city-controls-note'), /Decoration only/);
    assert.match(
      await text('city-controls-note'),
      /change nothing about\s+Missions, agents or their work/,
    );
    assert.match(await text('city-controls-note'), /no weather service or location/);
    assert.equal(await pressed('city-lighting-day'), 'true');
    assert.equal(await pressed('city-weather-off'), 'true');
    assert.match(await text('city-lighting-status'), /as it always looked/);
    assert.match(await text('city-weather-status'), /Clear skies/);
    // ten real toggle buttons (lighting 4, weather 3, traffic 3), all reachable by keyboard
    const buttons = await p().$$eval('[data-testid="city-controls"] button', (els) =>
      els.map((e) => e.getAttribute('aria-pressed')),
    );
    assert.equal(buttons.length, 10);
    assert.equal(await pressed('city-traffic-normal'), 'true'); // traffic is on by default: the street is alive
    assert.ok(buttons.every((v) => v === 'true' || v === 'false'));
    // Escape closes the panel
    await p().focus('[data-testid="city-lighting-night"]');
    await p().keyboard.press('Escape');
    assert.equal(await p().$('[data-testid="city-controls"]'), null);
    assert.equal(
      await p().$eval('[data-testid="city-button"]', (el) => el.getAttribute('aria-expanded')),
      'false',
    );
  },
  STEPS_MS,
);

test(
  'night darkens the city and keeps the lit things lit; dusk is between; day is EXACTLY the original look',
  async () => {
    await boot();
    // the default Office framing is almost entirely INDOORS (offices stay lit by design); judge the
    // lighting on open streets, where it has something to do
    await goToDistrict('Grand Boulevard');
    const day0 = await metrics();
    await openCity();
    await shot('living-city-day');
    await choose('city-lighting-dusk');
    const dusk = await metrics();
    await shot('living-city-dusk');
    await choose('city-lighting-night');
    const night = await metrics();
    await shot('living-city-night');
    assert.equal(await pressed('city-lighting-night'), 'true');
    assert.equal(await pressed('city-lighting-day'), 'false');
    // darker overall, in steps
    assert.ok(dusk.mean < day0.mean, `dusk ${dusk.mean} should be darker than day ${day0.mean}`);
    assert.ok(
      night.mean < dusk.mean,
      `night ${night.mean} should be darker than dusk ${dusk.mean}`,
    );
    assert.ok(
      night.mean < day0.mean * 0.8,
      `night ${night.mean} should be clearly darker than day ${day0.mean}`,
    );
    // ...but what is lit stays lit: the brightest 1% (offices, agents, lamps) keeps most of its brightness
    assert.ok(night.p99 >= day0.p99 * 0.6, `lit content fell from ${day0.p99} to ${night.p99}`);
    // and back to day: pixel-for-pixel the original look, no residue of the layer
    await choose('city-lighting-day');
    const day1 = await metrics();
    assert.equal(
      day1.sig,
      day0.sig,
      'day must be exactly the city as it was before lighting existed',
    );
  },
  STEPS_MS,
);

test(
  'rain and snow add pixels over the city and leave no residue when cleared',
  async () => {
    await boot();
    await goToDistrict('Mirror Lakes'); // open air, where weather has something to do
    await openCity();
    const clear = await metrics();
    await choose('city-weather-rain');
    const rain = await metrics();
    await shot('living-city-rain');
    assert.equal(await pressed('city-weather-rain'), 'true');
    assert.notEqual(rain.sig, clear.sig, 'rain must change the picture');
    assert.match(
      await text('city-weather-status'),
      /Rain over the outdoors only; indoors stays dry\. Held still: reduced motion is on\./,
    );
    await choose('city-weather-snow');
    const snow = await metrics();
    await shot('living-city-snow');
    assert.notEqual(snow.sig, rain.sig);
    assert.notEqual(snow.sig, clear.sig);
    // the weather is a small decoration, not a wash
    assert.ok(
      Math.abs(rain.mean - clear.mean) < clear.mean * 0.25,
      'rain should dim and decorate the outdoors, not repaint the picture',
    );
    await choose('city-weather-off');
    assert.equal(
      (await metrics()).sig,
      clear.sig,
      'clear skies are exactly the picture with no weather',
    );
  },
  STEPS_MS,
);

test(
  'night, rain, agents, labels and the inspector are all legible together',
  async () => {
    await boot();
    await openCity();
    await choose('city-lighting-night');
    await choose('city-weather-rain');
    // select a real (fixture) agent exactly as the canvas click handler does
    await p().evaluate(() => {
      const office = (
        window as unknown as {
          __diHarnessOffice: { selectedAgentId: number | null; cameraFollowId: number | null };
        }
      ).__diHarnessOffice;
      office.selectedAgentId = 9;
      office.cameraFollowId = 9;
    });
    await settle(10);
    await shot('living-city-night-rain-selected-agent');
    // the DOM overlays are above the canvas and untouched by the layer: the always-visible note is still there
    assert.match(await text('city-controls-note'), /Decoration only/);
    const panel = await p().$(
      '[data-testid="di-office-panel"], [data-testid="di-office-panel-collapsed"]',
    );
    assert.ok(panel, 'the office panel is still drawn');
  },
  STEPS_MS,
);

test(
  'the choice is saved, survives a reload, and a planted bad record is ignored',
  async () => {
    await boot();
    await openCity();
    await choose('city-lighting-night');
    await choose('city-weather-snow');
    const night = await metrics();
    // every choice was saved through the closed message, exactly the contract's four keys
    assert.deepEqual(
      saves.map((m) => m.state),
      [
        { v: 1, lighting: 'night', weather: 'off', traffic: 'normal' },
        { v: 1, lighting: 'night', weather: 'snow', traffic: 'normal' },
      ],
    );
    // and the real module wrote it under the owned root, and nowhere else
    assert.deepEqual(readCityPresentation(), {
      v: 1,
      lighting: 'night',
      weather: 'snow',
      traffic: 'normal',
    });
    assert.deepEqual(fs.readdirSync(stateRoot), [CITY_PRESENTATION_FILE_NAME]);

    // a fresh page: the saved choice is applied before anything is touched
    await boot();
    await openCity();
    assert.equal(await pressed('city-lighting-night'), 'true');
    assert.equal(await pressed('city-weather-snow'), 'true');
    assert.equal(await pressed('city-lighting-day'), 'false');
    // the city is applied: night and snow in the OfficeState that draws it, and as dark as before. (The
    // canvas is not compared pixel for pixel: the fixture's agents are seated at random on every load.)
    const applied = await p().evaluate(() => {
      const o = (
        window as unknown as {
          __diHarnessOffice: { city: { level: number; weather: { kind: string } } };
        }
      ).__diHarnessOffice;
      return { level: o.city.level, weather: o.city.weather.kind };
    });
    assert.deepEqual(applied, { level: 1, weather: 'snow' });
    assert.ok(
      Math.abs((await metrics()).mean - night.mean) < night.mean * 0.05,
      'the reloaded city is as dark as it was',
    );
    assert.equal(saves.length, 2, 'loading a saved choice must not save it again');

    // a hand-planted record that fails the contract is ignored whole: the defaults stand
    fs.writeFileSync(
      path.join(stateRoot, CITY_PRESENTATION_FILE_NAME),
      JSON.stringify({ v: 1, lighting: 'night', weather: 'hail', traffic: 'normal' }),
    );
    await boot();
    await openCity();
    assert.equal(await pressed('city-lighting-day'), 'true');
    assert.equal(await pressed('city-weather-off'), 'true');
  },
  STEPS_MS,
);

test(
  'with motion allowed, rain falls and the lighting fades in; with reduced motion it holds still',
  async () => {
    await boot('no-preference');
    await openCity();
    await choose('city-weather-rain');
    const ys = () =>
      p().evaluate(
        () =>
          (window as unknown as { __diHarnessOffice: { city: { weather: { ys: Float32Array } } } })
            .__diHarnessOffice.city.weather.ys[0],
      );
    const a = await ys();
    await p().waitForTimeout(400);
    const b = await ys();
    assert.notEqual(a, b, 'rain must move when motion is allowed');
    assert.match(
      await text('city-weather-status'),
      /Rain over the outdoors only; indoors stays dry\.$/,
    );
    // the lighting fades rather than jumping
    await p().focus('[data-testid="city-lighting-night"]');
    await p().keyboard.press('Enter');
    const levels: number[] = [];
    for (let k = 0; k < 8; k++) {
      levels.push(
        await p().evaluate(
          () =>
            (window as unknown as { __diHarnessOffice: { city: { level: number } } })
              .__diHarnessOffice.city.level,
        ),
      );
      await p().waitForTimeout(150);
    }
    assert.ok(
      levels.some((v) => v > 0 && v < 1),
      `expected a fade, saw ${levels.join(', ')}`,
    );
    assert.equal(levels[levels.length - 1], 1);
    // the preference turns on live: rain stops where it is
    await p().emulateMedia({ reducedMotion: 'reduce' });
    await p().waitForTimeout(300);
    const c = await ys();
    await p().waitForTimeout(400);
    assert.equal(await ys(), c, 'reduced motion must hold the rain still');
  },
  STEPS_MS,
);

test(
  "the clock mode follows this computer's local time and says so",
  async () => {
    await boot();
    await openCity();
    await choose('city-lighting-clock');
    assert.equal(await pressed('city-lighting-clock'), 'true');
    const status = await text('city-lighting-status');
    assert.match(status, /Following this computer's clock \(\d\d:\d\d\): (day|dusk|night)/);
    const now = await p().evaluate(() => new Date().toTimeString().slice(0, 5));
    assert.ok(
      status.includes(`(${now})`) ||
        Math.abs(Number(status.match(/\((\d\d):(\d\d)\)/)![2]) - Number(now.slice(3))) <= 1,
      `status ${status} vs browser clock ${now}`,
    );
    // nothing but the mode is persisted: no time, no stage
    assert.deepEqual(readCityPresentation(), {
      v: 1,
      lighting: 'clock',
      weather: 'off',
      traffic: 'normal',
    });
  },
  STEPS_MS,
);
