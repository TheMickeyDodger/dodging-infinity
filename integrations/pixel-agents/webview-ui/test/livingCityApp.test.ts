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

import {
  checkCityPresentationV2,
  CITY_PRESENTATION_V2_MAX_BYTES,
  packCityPresentationV2,
  unpackCityPresentationV2,
} from '../../core/src/cityPresentation.ts';
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
  'the City panel opens from the toolbar, says what it is, and starts with Day lighting, Automatic seasons (summer) and Automatic regional weather',
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
    // item 18: the default is Automatic seasons (starting in summer, 30 minutes each) and Automatic regional weather
    assert.equal(await pressed('city-season-auto'), 'true');
    assert.equal(await pressed('city-weather-auto'), 'true');
    assert.equal(await pressed('city-season-summer'), 'false');
    assert.match(await text('city-lighting-status'), /as it always looked/);
    assert.match(await text('city-season-status'), /^Automatic · Summer · (29|30) min left/);
    assert.match(await text('city-weather-status'), /^Automatic regional weather: /);
    // THE EXACT INVENTORY of the panel, in markup order: every control with its element, accessible name and state. Eighteen choices are real
    // toggle buttons (aria-pressed, default states below); the Close action is a plain button (no aria-pressed) with an accessible name; the
    // disclosure is a <summary> (no aria-pressed) of a closed <details>; the groups are named; the heading is a heading.
    const inventory = await p().$$eval(
      '[data-testid="city-controls"] button, [data-testid="city-controls"] summary',
      (els) =>
        els.map((e) => [
          e.getAttribute('data-testid'),
          e.tagName,
          e.getAttribute('aria-label') ?? (e.textContent ?? '').trim(),
          e.getAttribute('aria-pressed'),
          (e as HTMLButtonElement).disabled === true,
        ]),
    );
    assert.deepEqual(inventory, [
      ['city-controls-close', 'BUTTON', 'Close the City panel', null, false],
      ['city-lighting-day', 'BUTTON', 'Day', 'true', false],
      ['city-lighting-dusk', 'BUTTON', 'Dusk', 'false', false],
      ['city-lighting-night', 'BUTTON', 'Night', 'false', false],
      ['city-lighting-clock', 'BUTTON', 'Clock', 'false', false],
      ['city-season-auto', 'BUTTON', 'Automatic', 'true', false],
      ['city-season-spring', 'BUTTON', 'Spring', 'false', false],
      ['city-season-summer', 'BUTTON', 'Summer', 'false', false],
      ['city-season-autumn', 'BUTTON', 'Autumn', 'false', false],
      ['city-season-winter', 'BUTTON', 'Winter', 'false', false],
      ['city-season-pause', 'BUTTON', 'Pause seasons', 'false', false],
      ['city-weather-auto', 'BUTTON', 'Automatic', 'true', false],
      ['city-weather-clear', 'BUTTON', 'Clear', 'false', false],
      ['city-weather-rain', 'BUTTON', 'Rain', 'false', false],
      ['city-weather-light-snow', 'BUTTON', 'Light snow', 'false', false],
      ['city-weather-heavy-snow', 'BUTTON', 'Heavy snow', 'false', false],
      ['city-atmosphere-details-summary', 'SUMMARY', 'How seasons and weather work', null, false],
      ['city-traffic-off', 'BUTTON', 'Off', 'false', false],
      ['city-traffic-low', 'BUTTON', 'Low', 'false', false],
      ['city-traffic-normal', 'BUTTON', 'Normal', 'true', false],
    ]);
    // the structure around them: a heading, the named groups, the live status lines, the closed disclosure, and the current season marked
    const structure = await p().evaluate(() => {
      const q = (id: string) => document.querySelector(`[data-testid="${id}"]`);
      const panel = q('city-controls')!;
      return {
        heading: [q('city-controls-heading')!.tagName, (q('city-controls-heading')!.textContent ?? '').trim()],
        groups: [...panel.querySelectorAll('[role="group"]')].map((g) => g.getAttribute('aria-label')),
        panelRole: [panel.getAttribute('role'), panel.getAttribute('aria-label')],
        liveStatus: ['city-lighting-status', 'city-season-status'].map((id) => [id, q(id)!.getAttribute('role'), q(id)!.getAttribute('aria-live')]),
        detailsOpen: (q('city-atmosphere-details') as HTMLDetailsElement).open,
        currentSeason: [...panel.querySelectorAll('[aria-current="true"]')].map((e) => e.getAttribute('data-testid')),
      };
    });
    assert.deepEqual(structure, {
      heading: ['H2', 'City: lighting, seasons and weather'],
      groups: ['Lighting', 'Season', 'Weather', 'Traffic'],
      panelRole: ['group', 'City lighting and weather'],
      liveStatus: [
        ['city-lighting-status', 'status', 'polite'],
        ['city-season-status', 'status', 'polite'],
      ],
      detailsOpen: false,
      currentSeason: ['city-season-summer'],
    });
    // the focus order is the markup order above (walked key by key in the viewport tests below); opening the disclosure shows its words
    await p().focus('[data-testid="city-atmosphere-details-summary"]');
    await p().keyboard.press('Enter');
    assert.equal(await p().$eval('[data-testid="city-atmosphere-details"]', (el) => (el as HTMLDetailsElement).open), true);
    assert.match(
      await p().$eval('[data-testid="city-atmosphere-details"]', (el) => (el as HTMLElement).innerText),
      /Pause keeps the\s+time left/,
    );
    await p().keyboard.press('Enter'); // and closes again
    assert.equal(await pressed('city-traffic-normal'), 'true'); // traffic is on by default: the street is alive
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
    await openCity();
    // the original look has no weather: the default is now Automatic regional weather, so the premise is made explicit (reduced
    // motion swaps to Clear at once; summer is the original art)
    await choose('city-weather-clear');
    const day0 = await metrics();
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
    await choose('city-weather-clear'); // the default is Automatic weather: Clear is the picture with no weather
    const clear = await metrics();
    await choose('city-weather-rain');
    const rain = await metrics();
    await shot('living-city-rain');
    assert.equal(await pressed('city-weather-rain'), 'true');
    assert.notEqual(rain.sig, clear.sig, 'rain must change the picture');
    assert.match(
      await text('city-weather-status'),
      /Rain everywhere\. Held until you choose Automatic\. Indoors stays dry\. Held still: reduced motion is on\./,
    );
    await choose('city-weather-light-snow');
    const snow = await metrics();
    await shot('living-city-snow');
    assert.notEqual(snow.sig, rain.sig);
    assert.notEqual(snow.sig, clear.sig);
    // the weather is a small decoration, not a wash
    assert.ok(
      Math.abs(rain.mean - clear.mean) < clear.mean * 0.25,
      'rain should dim and decorate the outdoors, not repaint the picture',
    );
    await choose('city-weather-clear');
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

/** The shape of a saved city record (contract v2), for reading it in these tests. */
interface CityRecordLike {
  v: number;
  lighting: string;
  traffic: string;
  season: { mode: string; season: string; paused: boolean; remaining: number };
  weather: {
    mode: string;
    seed: number;
    regions: Array<{ kind: string; step: number; wet: number; remaining: number }>;
  };
}
/** The OfficeState of the harness page, as plain values: the record a reload needs. */
const cityRecord = (): Promise<CityRecordLike> =>
  p().evaluate(
    () =>
      (window as unknown as { __diHarnessOffice: { cityRecord(): CityRecordLike } }).__diHarnessOffice.cityRecord(),
  );
const cityFile = () => path.join(stateRoot, CITY_PRESENTATION_FILE_NAME);
const plant = (record: unknown) => {
  fs.mkdirSync(stateRoot, { recursive: true });
  fs.writeFileSync(cityFile(), typeof record === 'string' ? record : JSON.stringify(record));
};
/** A record as the server stores it (the compact form), as a planted file. */
const stored = (record: unknown): string =>
  JSON.stringify(packCityPresentationV2(record as Parameters<typeof packCityPresentationV2>[0]));
/** A valid v2 record with a held winter, Heavy snow and quiet regions (the shape the office itself saves). */
const v2Record = (over: Record<string, unknown> = {}) => ({
  v: 2,
  lighting: 'dusk',
  traffic: 'low',
  season: { mode: 'auto', season: 'autumn', paused: false, remaining: 1_000_000 },
  weather: {
    mode: 'auto',
    seed: 0x51a7,
    regions: [0, 1, 2, 3, 4].map((k) => ({ kind: 'clear', step: 10 + k, wet: 0, remaining: 250_000 })),
  },
  ...over,
});

test(
  'the choice is saved, survives a reload, and a planted bad record is ignored',
  async () => {
    await boot();
    await openCity();
    await choose('city-lighting-night');
    await choose('city-weather-light-snow');
    const night = await metrics();
    // every choice was saved through the closed message as one strict v2 record
    assert.equal(saves.length, 2);
    for (const m of saves) assert.equal(checkCityPresentationV2(m.state), null);
    const [first, second] = saves.map((m) => m.state as unknown as CityRecordLike);
    assert.deepEqual([first.v, first.lighting, first.traffic, first.weather.mode], [2, 'night', 'normal', 'auto']);
    assert.deepEqual(
      [second.v, second.lighting, second.traffic, second.weather.mode],
      [2, 'night', 'normal', 'light-snow'],
    );
    assert.deepEqual(second.season, { mode: 'auto', season: 'summer', paused: false, remaining: second.season.remaining });
    assert.deepEqual(second.weather.regions.map((r) => r.kind), ['light-snow', 'light-snow', 'light-snow', 'light-snow', 'light-snow']);
    // and the real module wrote exactly that under the owned root, and nowhere else
    assert.deepEqual(readCityPresentation(), second);
    assert.deepEqual(fs.readdirSync(stateRoot), [CITY_PRESENTATION_FILE_NAME]);

    // a fresh page: the saved record is applied before anything is touched
    await boot();
    await openCity();
    assert.equal(await pressed('city-lighting-night'), 'true');
    assert.equal(await pressed('city-weather-light-snow'), 'true');
    assert.equal(await pressed('city-lighting-day'), 'false');
    // the city is applied: night and snow in the OfficeState that draws it, and as dark as before. (The
    // canvas is not compared pixel for pixel: the fixture's agents are seated at random on every load.)
    const applied = await p().evaluate(() => {
      const o = (
        window as unknown as {
          __diHarnessOffice: { city: { level: number; regional: { mode: string } } };
        }
      ).__diHarnessOffice;
      return { level: o.city.level, weather: o.city.regional.mode };
    });
    assert.deepEqual(applied, { level: 1, weather: 'light-snow' });
    assert.ok(
      Math.abs((await metrics()).mean - night.mean) < night.mean * 0.05,
      'the reloaded city is as dark as it was',
    );
    assert.equal(saves.length, 2, 'loading a saved record must not save it again');

    // a hand-planted record that fails the contract is ignored whole: the defaults stand, and the file is left as it was
    const bad = JSON.stringify({ v: 1, lighting: 'night', weather: 'hail', traffic: 'normal' });
    plant(bad);
    await boot();
    await openCity();
    assert.equal(await pressed('city-lighting-day'), 'true');
    assert.equal(await pressed('city-weather-auto'), 'true'); // the defaults stand: Automatic regional weather
    assert.equal(await pressed('city-season-auto'), 'true');
    assert.equal(fs.readFileSync(cityFile(), 'utf-8'), bad, 'an ignored record is not rewritten');
    assert.deepEqual(fs.readdirSync(stateRoot), [CITY_PRESENTATION_FILE_NAME]);
  },
  STEPS_MS,
);

test(
  'THE WHOLE CITY SURVIVES A RELOAD: a paused Automatic winter, Heavy snow, dusk and low traffic come back exactly, and loading saves nothing',
  async () => {
    await boot();
    await openCity();
    await choose('city-season-winter'); // held
    await choose('city-season-auto'); // a fresh full interval from winter
    await choose('city-season-pause');
    await choose('city-weather-heavy-snow');
    await choose('city-lighting-dusk');
    await choose('city-traffic-low');
    assert.match(await text('city-season-status'), /^Paused on Winter, (29|30) min left/);
    const before = await cityRecord();
    assert.equal(checkCityPresentationV2(before), null);
    assert.deepEqual(before.season.mode, 'auto');
    assert.equal(before.season.paused, true);
    assert.equal(before.weather.mode, 'heavy-snow');
    const savesBefore = saves.length;
    assert.deepEqual(readCityPresentation(), before, 'the last save is the whole city as it stood');
    // the file the server wrote is the compact stored record, inside the 256-byte budget
    const onDisk = fs.statSync(cityFile()).size;
    console.log(`MEASURED [persisted v2 record, paused Automatic winter + Heavy snow] ${onDisk} bytes on disk, cap ${CITY_PRESENTATION_V2_MAX_BYTES}`);
    assert.ok(onDisk <= CITY_PRESENTATION_V2_MAX_BYTES && CITY_PRESENTATION_V2_MAX_BYTES === 256);

    await boot(); // reload (the harness replaces the page)
    await openCity();
    assert.equal(await pressed('city-season-auto'), 'true');
    assert.equal(await pressed('city-season-pause'), 'true');
    assert.equal(await pressed('city-weather-heavy-snow'), 'true');
    assert.equal(await pressed('city-lighting-dusk'), 'true');
    assert.equal(await pressed('city-traffic-low'), 'true');
    assert.match(await text('city-season-status'), /^Paused on Winter, (29|30) min left/);
    // paused season, held weather: nothing moved, so the record is EXACTLY the one saved
    assert.deepEqual(await cityRecord(), before);
    assert.equal(saves.length, savesBefore, 'loading must not save');
  },
  STEPS_MS,
);

test(
  'NO CATCH-UP FOR A CLOSED VIEW: a saved schedule continues from its saved time left, however long the wall clock ran while the view was closed',
  async () => {
    plant(stored(v2Record()));
    await p()?.close();
    page = null;
    // the view is "closed" for a while: the wall clock runs, nothing else does
    await new Promise((resolve) => setTimeout(resolve, 9_000));
    await boot();
    const record = await cityRecord();
    // the season and every region lost only the page's own few seconds of running time, never the nine seconds the view was closed
    assert.ok(record.season.remaining <= 1_000_000 && record.season.remaining >= 1_000_000 - 6_000, `season time left ${record.season.remaining}`);
    assert.equal(record.season.season, 'autumn');
    for (const [k, r] of record.weather.regions.entries()) {
      assert.equal(r.step, 10 + k, `region ${k} drew nothing`);
      assert.ok(r.remaining <= 250_000 && r.remaining >= 250_000 - 6_000, `region ${k} time left ${r.remaining}`);
    }
  },
  STEPS_MS,
);

test(
  'A SAVED v1 RECORD IS MIGRATED ON LOAD (lighting, traffic and weather kept, Automatic seasons from summer), reading writes nothing, and the next choice writes v2',
  async () => {
    const old = JSON.stringify({ v: 1, lighting: 'night', weather: 'snow', traffic: 'low' });
    plant(old);
    await boot();
    await openCity();
    assert.equal(await pressed('city-lighting-night'), 'true');
    assert.equal(await pressed('city-traffic-low'), 'true');
    assert.equal(await pressed('city-weather-light-snow'), 'true'); // the saved v1 snow is the manual Light snow
    assert.equal(await pressed('city-season-auto'), 'true');
    assert.match(await text('city-season-status'), /^Automatic · Summer · (29|30) min left/);
    const record = await cityRecord();
    assert.equal(record.weather.mode, 'light-snow');
    assert.equal(fs.readFileSync(cityFile(), 'utf-8'), old, 'reading a v1 file wrote nothing');
    assert.equal(saves.length, 0);
    await choose('city-lighting-day');
    assert.equal(saves.length, 1);
    // what the server wrote is a STORED v2 record: it unpacks to a valid named record that is the migrated city plus the new choice
    const read = unpackCityPresentationV2(JSON.parse(fs.readFileSync(cityFile(), 'utf-8')));
    assert.ok(read.ok);
    const written = read.ok ? read.state : null;
    assert.equal(written?.v, 2);
    assert.equal(checkCityPresentationV2(written), null);
    assert.equal(written?.weather.mode, 'light-snow');
    assert.equal(written?.lighting, 'day');
    assert.equal(written?.traffic, 'low');
  },
  STEPS_MS,
);

test(
  'AN INVALID v2 RECORD (four regions) IS IGNORED WHOLE: the defaults stand, the file is untouched and nothing else is written',
  async () => {
    const packed = packCityPresentationV2(v2Record() as Parameters<typeof packCityPresentationV2>[0]);
    packed.w[2].pop(); // four regions
    const bad = JSON.stringify(packed);
    plant(bad);
    await boot();
    await openCity();
    assert.equal(await pressed('city-season-auto'), 'true');
    assert.equal(await pressed('city-weather-auto'), 'true');
    assert.equal(await pressed('city-lighting-day'), 'true');
    assert.equal(fs.readFileSync(cityFile(), 'utf-8'), bad);
    assert.deepEqual(fs.readdirSync(stateRoot), [CITY_PRESENTATION_FILE_NAME]);
    assert.equal(saves.length, 0);
  },
  STEPS_MS,
);

test(
  'SAVES ARE BOUNDED: a burst of season and weather transitions is a couple of saves, a hidden view is saved once, and an idle city saves nothing',
  async () => {
    await boot();
    await openCity();
    await choose('city-lighting-dusk'); // one save, and the gate starts from it
    const base = saves.length;
    // 25 season changes in well under a second: the gate waits out its minimum gap, so this is a save or two, never 25
    await p().evaluate(() => {
      const o = (window as unknown as { __diHarnessOffice: { city: { seasons: { restore(s: object): void; update(dt: number, r: boolean): boolean } } } })
        .__diHarnessOffice;
      for (let k = 0; k < 25; k++) {
        o.city.seasons.restore({ mode: 'auto', season: 'summer', paused: false, remaining: 100 });
        o.city.seasons.update(0.25, true);
      }
    });
    await p().waitForTimeout(3_500);
    const burst = saves.length - base;
    assert.ok(burst >= 1 && burst <= 2, `${burst} saves for a burst of 25 transitions`);
    // the saved record is the city as it stands (a valid v2 record)
    assert.equal(checkCityPresentationV2(saves[saves.length - 1].state), null);
    // hiding the view saves once more (a clock is running), and hiding again straight away saves nothing more (the minimum gap)
    await p().waitForTimeout(2_100);
    const beforeHide = saves.length;
    const hide = () =>
      p().evaluate(() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
        document.dispatchEvent(new Event('visibilitychange'));
      });
    await hide();
    await p().waitForTimeout(300);
    assert.equal(saves.length, beforeHide + 1, 'hiding saves once');
    await hide();
    await p().waitForTimeout(300);
    assert.equal(saves.length, beforeHide + 1, 'hiding again at once saves nothing more');
  },
  STEPS_MS,
);

test(
  "HIDDEN AND VISIBLE in the real App [SYNTHETIC lifecycle event: an injected visibilityState plus a dispatched visibilitychange, not native tab backgrounding or the browser's own rAF throttling]: the city's clocks stand still while the view is hidden, the hiding is saved once as the city stood, and the resume carries on with no catch-up",
  async () => {
    await boot('no-preference'); // motion allowed: the clocks run in real time
    await p().waitForTimeout(2_500); // past the saver's minimum gap, so hiding is allowed to save
    const base = saves.length;
    const setVisibility = (state: 'hidden' | 'visible') =>
      p().evaluate((value) => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
        document.dispatchEvent(new Event('visibilitychange'));
      }, state);
    // the record is read in the SAME page task that hides the view, so no frame can fall between the two
    const before = await p().evaluate((): CityRecordLike => {
      const o = (window as unknown as { __diHarnessOffice: { cityRecord(): CityRecordLike } }).__diHarnessOffice;
      const record = o.cityRecord();
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
      return record;
    });
    // hiding again straight away saves nothing more (the saver's minimum gap)
    await setVisibility('hidden');
    await p().waitForTimeout(300);
    assert.equal(saves.length, base + 1, 'hiding again at once saved nothing more');
    await p().waitForTimeout(3_700); // four real seconds hidden in all
    const during = await cityRecord();
    assert.deepEqual(during.season, before.season, 'the season clock stood still while hidden');
    assert.deepEqual(during.weather.regions, before.weather.regions, 'every region stood still while hidden (kind, draw index, streak and time left)');
    assert.equal(saves.length, base + 1, 'hiding saved exactly once, and nothing was saved while hidden');
    assert.deepEqual(saves[saves.length - 1].state, before, 'and what it saved is the city exactly as it stood when it was hidden');
    // the resume: it carries on from where it stood, with the four hidden seconds NOT caught up
    await setVisibility('visible');
    await p().waitForTimeout(1_000);
    const after = await cityRecord();
    const used = before.season.remaining - after.season.remaining;
    assert.ok(used > 0 && used < 2_500, `the season used ${used} ms of running time in about one visible second, not the four hidden ones`);
    for (const [k, r] of after.weather.regions.entries()) {
      const was = before.weather.regions[k];
      if (r.step === was.step) {
        const spent = was.remaining - r.remaining;
        assert.ok(spent > 0 && spent < 2_500, `region ${k} used ${spent} ms`);
      } else assert.equal(r.step, was.step + 1, `region ${k} drew once`);
    }
    assert.equal(after.season.season, before.season.season);
  },
  STEPS_MS,
);

/** The tab order of the City panel, from its MARKUP (not from box geometry): the Close action first, then every enabled control in reading order. */
const PANEL_TAB_ORDER = [
  'city-controls-close',
  'city-lighting-day',
  'city-lighting-dusk',
  'city-lighting-night',
  'city-lighting-clock',
  'city-season-auto',
  'city-season-spring',
  'city-season-summer',
  'city-season-autumn',
  'city-season-winter',
  'city-season-pause',
  'city-weather-auto',
  'city-weather-clear',
  'city-weather-rain',
  'city-weather-light-snow',
  'city-weather-heavy-snow',
  'city-atmosphere-details-summary',
  'city-traffic-off',
  'city-traffic-low',
  'city-traffic-normal',
];

for (const [width, height] of [
  [1600, 1000],
  [1280, 720],
  [1024, 600],
  [800, 480],
  [640, 360],
  [1100, 300],
] as const) {
  test(
    `THE CITY PANEL FITS THE VIEWPORT at ${width} x ${height}: heading and Close always in view, every control reachable by scroll and by keyboard, visible focus, Escape closes, no horizontal overflow, one panel`,
    async () => {
      await boot();
      await p().setViewportSize({ width, height });
      // READINESS: the ambient legend mounts about 410 ms after the page is ready (measured: 417, 411 and 412 ms over three fresh boots) and takes height from the City panel, so the
      // panel's fit is measured only once it is there, in the steady state a person sees. BEFORE this the test waited 300 ms and measured at about the moment the legend mounted, so
      // its outcome depended on that race (22 of 22 in one run, 21 of 22 in the next). This only fixes WHICH state is measured; no assertion below changed.
      await p().waitForSelector('[data-testid="ambient-legend"]', { state: 'attached', timeout: 30_000 });
      await p().waitForTimeout(300);
      // With the office open it takes 420 px of the window. Where that leaves a world narrower than the toolbar (640 wide leaves 220 px) the world's
      // controls, the City button among them, are REMOVED until the office is minimized (WorldControlsGate, diPanelLayout.ts), so the City panel is
      // reached after Minimize. Only that precondition is added: the viewport and every assertion below are unchanged. The condition is the rendered
      // fact (the City button is not available), never a hardcoded width.
      if (!(await p().locator('[data-testid="city-button"]').isVisible())) {
        assert.equal(
          await p().evaluate(() => document.querySelector('[data-di-world-controls]')?.getAttribute('data-di-world-controls')),
          'yielded',
          'the City button is unavailable because the world yielded its controls, not for any other reason',
        );
        await p().click('[data-testid="di-office-panel-minimize"]');
        await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
        await p().waitForTimeout(300);
      }
      await openCity();
      const inside = (r: { top: number; left: number; bottom: number; right: number }, box: { top: number; left: number; bottom: number; right: number }) =>
        r.top >= box.top - 0.5 && r.left >= box.left - 0.5 && r.bottom <= box.bottom + 0.5 && r.right <= box.right + 0.5;
      const geometry = () =>
        p().evaluate(() => {
          const rect = (el: Element | null) => {
            const r = el!.getBoundingClientRect();
            return { top: r.top, left: r.left, bottom: r.bottom, right: r.right };
          };
          const q = (id: string) => document.querySelector(`[data-testid="${id}"]`);
          const scroll = q('city-controls-scroll') as HTMLElement;
          return {
            viewport: { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth },
            panel: rect(q('city-controls')),
            heading: rect(q('city-controls-heading')),
            close: rect(q('city-controls-close')),
            scroll: rect(scroll),
            overflowY: getComputedStyle(scroll).overflowY,
            scrollable: scroll.scrollHeight > scroll.clientHeight + 1,
            horizontal: scroll.scrollWidth > scroll.clientWidth + 1 || document.documentElement.scrollWidth > window.innerWidth,
            panels: document.querySelectorAll('[data-testid="city-controls"]').length,
            dialogs: document.querySelectorAll('[role="dialog"]').length,
          };
        });
      const g0 = await geometry();
      // NOTHING IS ABOVE THE SCREEN: the whole panel is inside the viewport, and its heading and Close action with it
      assert.ok(inside(g0.panel, g0.viewport), `the panel ${JSON.stringify(g0.panel)} is inside the ${width} x ${height} viewport`);
      assert.ok(inside(g0.heading, g0.viewport) && inside(g0.close, g0.viewport));
      assert.equal(g0.overflowY, 'auto');
      assert.equal(g0.horizontal, false, 'no horizontal overflow');
      assert.equal(g0.panels, 1, 'one coherent control surface');
      assert.equal(g0.dialogs, 0, 'no second competing dialog');
      if (height <= 720) assert.equal(g0.scrollable, true, 'a short window scrolls inside the panel');
      // the intro is at the top of the scroll region and visible at scrollTop 0
      const introTop = await p().evaluate(() => {
        const scroll = document.querySelector('[data-testid="city-controls-scroll"]') as HTMLElement;
        scroll.scrollTop = 0;
        const note = document.querySelector('[data-testid="city-controls-note"]')!.getBoundingClientRect();
        const box = scroll.getBoundingClientRect();
        return note.top >= box.top - 0.5 && note.bottom <= box.bottom + 0.5;
      });
      assert.equal(introTop, true, 'the intro is readable at the top');

      // EVERY CONTROL, STATUS LINE AND THE DISCLOSURE IS REACHABLE BY SCROLL: after scrolling it into view it lies inside the panel's scroll region and the viewport
      const targets = [...PANEL_TAB_ORDER.map((id) => (id === 'city-atmosphere-details-summary' ? 'city-atmosphere-details' : id)), 'city-lighting-status', 'city-season-status', 'city-weather-status', 'city-traffic-status', 'city-traffic-note'];
      for (const id of targets) {
        const r = await p().evaluate((testid) => {
          const el = document.querySelector(`[data-testid="${testid}"]`) as HTMLElement;
          if (testid !== 'city-controls-close') el.scrollIntoView({ block: 'nearest' });
          const rect = el.getBoundingClientRect();
          const box = (document.querySelector('[data-testid="city-controls-scroll"]') as HTMLElement).getBoundingClientRect();
          const head = (document.querySelector('[data-testid="city-controls-close"]') as HTMLElement).getBoundingClientRect();
          const bounds = testid === 'city-controls-close' ? { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth } : box;
          return {
            rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
            bounds: { top: bounds.top, left: bounds.left, bottom: bounds.bottom, right: bounds.right },
            headBottom: head.bottom,
          };
        }, id);
        assert.ok(inside(r.rect, r.bounds), `${id} ${JSON.stringify(r.rect)} is inside ${JSON.stringify(r.bounds)} once scrolled to`);
      }
      const gScrolled = await geometry();
      assert.ok(inside(gScrolled.heading, gScrolled.viewport) && inside(gScrolled.close, gScrolled.viewport), 'the heading and Close stay in view while the controls scroll');

      // THE KEYBOARD WALK: the tab stops are enumerated from the markup (a closed <details> contributes its <summary> only), and Tab visits exactly those, in order,
      // each one visible and showing a focus indicator
      const markupOrder = await p().evaluate(() => {
        const panel = document.querySelector('[data-testid="city-controls"]')!;
        return [...panel.querySelectorAll('button:not([disabled]), summary, [tabindex]:not([tabindex="-1"]), a[href], input, select, textarea')].map((el) => {
          const id = (el as HTMLElement).dataset.testid;
          return id ?? (el.tagName === 'SUMMARY' ? 'city-atmosphere-details-summary' : el.tagName);
        });
      });
      assert.deepEqual(markupOrder, PANEL_TAB_ORDER, 'the markup has exactly the expected tab stops, in reading order');
      await p().evaluate(() => (document.querySelector('[data-testid="city-controls-close"]') as HTMLElement).focus());
      const walked: string[] = [];
      for (let k = 0; k < PANEL_TAB_ORDER.length; k++) {
        const state = await p().evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const box = (document.querySelector('[data-testid="city-controls-scroll"]') as HTMLElement).getBoundingClientRect();
          const isClose = el.dataset.testid === 'city-controls-close';
          return {
            id: el.dataset.testid ?? (el.tagName === 'SUMMARY' ? 'city-atmosphere-details-summary' : el.tagName),
            rect: { top: r.top, left: r.left, bottom: r.bottom, right: r.right },
            bounds: isClose ? { top: 0, left: 0, bottom: window.innerHeight, right: window.innerWidth } : { top: box.top, left: box.left, bottom: box.bottom, right: box.right },
            focusVisible: el.matches(':focus-visible'),
            outline: getComputedStyle(el).outlineStyle !== 'none' && parseFloat(getComputedStyle(el).outlineWidth) > 0,
          };
        });
        assert.ok(state, 'something is focused');
        walked.push(state!.id);
        assert.ok(inside(state!.rect, state!.bounds), `${state!.id} is visible when it has focus (${JSON.stringify(state!.rect)} in ${JSON.stringify(state!.bounds)})`);
        if (k > 0) {
          assert.equal(state!.focusVisible, true, `${state!.id} matches :focus-visible after a Tab`);
          assert.equal(state!.outline, true, `${state!.id} draws a focus outline`);
        }
        await p().keyboard.press('Tab');
      }
      assert.deepEqual(walked, PANEL_TAB_ORDER, 'Tab visits every control once, in order');

      // Escape closes the panel; the Close button closes it too
      await p().focus('[data-testid="city-lighting-night"]');
      await p().keyboard.press('Escape');
      assert.equal(await p().$('[data-testid="city-controls"]'), null, 'Escape closes');
      await openCity();
      await p().click('[data-testid="city-controls-close"]');
      assert.equal(await p().$('[data-testid="city-controls"]'), null, 'Close closes');
      await shot(`living-city-panel-${width}x${height}`);
    },
    STEPS_MS,
  );
}

test(
  'with motion allowed, rain falls and the lighting fades in; with reduced motion it holds still',
  async () => {
    await boot('no-preference');
    await openCity();
    await choose('city-weather-rain');
    const ys = () =>
      p().evaluate(
        () =>
          (
            window as unknown as {
              __diHarnessOffice: { city: { regional: { rain: { ys: Float32Array } } } };
            }
          ).__diHarnessOffice.city.regional.rain.ys[0],
      );
    const a = await ys();
    await p().waitForTimeout(400);
    const b = await ys();
    assert.notEqual(a, b, 'rain must move when motion is allowed');
    assert.match(
      await text('city-weather-status'),
      /Rain everywhere\. Held until you choose Automatic\. Indoors stays dry\.$/,
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
    // the lighting MODE is persisted, never a time or a stage: the record has exactly its contract keys and no level or clock reading
    const saved = readCityPresentation() as unknown as CityRecordLike;
    assert.deepEqual(Object.keys(saved).sort(), ['lighting', 'season', 'traffic', 'v', 'weather']);
    assert.deepEqual([saved.v, saved.lighting, saved.traffic, saved.weather.mode], [2, 'clock', 'normal', 'auto']);
  },
  STEPS_MS,
);

/** The mean colour of the PALE pixels of the canvas (the ground; trees and buildings are darker), as hue, saturation, lightness. */
const paleGround = () =>
  p().evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement;
    const g = canvas.getContext('2d')!;
    const w = Math.floor(canvas.width * 0.6);
    const h = Math.floor(canvas.height * 0.6);
    const d = g.getImageData(Math.floor(canvas.width * 0.2), Math.floor(canvas.height * 0.2), w, h).data;
    let r = 0;
    let gg = 0;
    let b = 0;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      const max = Math.max(d[i], d[i + 1], d[i + 2]);
      const min = Math.min(d[i], d[i + 1], d[i + 2]);
      if ((max + min) / 510 < 0.62) continue; // lightness below 0.62: not snow
      r += d[i];
      gg += d[i + 1];
      b += d[i + 2];
      n++;
    }
    if (n === 0) return { n: 0, h: 0, s: 0, l: 0 };
    const rf = r / n / 255;
    const gf = gg / n / 255;
    const bf = b / n / 255;
    const max = Math.max(rf, gf, bf);
    const min = Math.min(rf, gf, bf);
    const l = (max + min) / 2;
    const dd = max - min;
    const s = dd === 0 ? 0 : l > 0.5 ? dd / (2 - max - min) : dd / (max + min);
    let hue = 0;
    if (dd !== 0) {
      if (max === rf) hue = ((gf - bf) / dd + (gf < bf ? 6 : 0)) * 60;
      else if (max === gf) hue = ((bf - rf) / dd + 2) * 60;
      else hue = ((rf - gf) / dd + 4) * 60;
    }
    return { n, h: Math.round(hue), s: Math.round(s * 100) / 100, l: Math.round(l * 100) / 100 };
  });

test(
  'WINTER GROUND READS AS SNOW in the Autumn Wildwood: cool and neutral, with no warm tint left from the district wash (so no warm/cool seam beside the Highlands), while summer keeps its rust',
  async () => {
    await boot('reduce'); // a season changes at once
    // the real DI office turns the district washes ON the first time it runs (server/src/di/diClientMessages.ts sets showAreas), and sends it
    // with the settings: the harness's fake server sends nothing, so say it the way the real office does
    socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, showAreas: true }));
    await settle(6);
    await goToDistrict('Autumn Wildwood');
    await openCity();
    await choose('city-weather-clear');
    await choose('city-season-summer');
    const summer = await paleGround();
    await choose('city-season-winter');
    const winter = await paleGround();
    await shot('living-city-wildwood-winter-ground');
    console.log(`MEASURED [Autumn Wildwood ground] summer ${JSON.stringify(summer)}; winter ${JSON.stringify(winter)}`);
    // winter: plenty of pale ground, and it is cool (blue side of the wheel) and nearly neutral, not rose or peach
    assert.ok(winter.n > 20_000, `${winter.n} pale ground pixels in view`);
    assert.ok(winter.h >= 190 && winter.h <= 250, `the winter ground hue is ${winter.h}, not cool`);
    assert.ok(winter.s <= 0.2, `the winter ground saturation is ${winter.s}: a tint is left`);
    assert.ok(winter.l >= 0.7, `the winter ground is snow-light (${winter.l})`);
    // summer is untouched: the ground keeps its warm rust, so there are few pale pixels and any that exist are warm
    assert.ok(summer.n < winter.n / 5, `summer has ${summer.n} pale pixels against winter's ${winter.n}`);
  },
  STEPS_MS,
);

const focusedTestId = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? null);
const cityOpen = async () => (await p().$('[data-testid="city-controls"]')) !== null;

test(
  'ESCAPE CLOSES THE CITY PANEL from the City button AND from inside the panel, after a reload, and focus ends on the City button (the Close button promises it)',
  async () => {
    await boot();
    await openCity();
    await choose('city-season-winter'); // something saved, so the second load below is a real reload of a saved city
    await boot(); // the reload
    // (1) opened with the mouse: focus stays on the City button, OUTSIDE the panel. Escape must close it from there.
    await p().click('[data-testid="city-button"]');
    await p().waitForSelector('[data-testid="city-controls"]');
    assert.equal(await focusedTestId(), 'city-button', 'focus is on the button that opened the panel');
    await p().keyboard.press('Escape');
    assert.equal(await cityOpen(), false, 'Escape on the City button closes the open panel');
    assert.equal(await p().$eval('[data-testid="city-button"]', (el) => el.getAttribute('aria-expanded')), 'false');
    assert.equal(await focusedTestId(), 'city-button', 'focus stays on the button');
    // Escape with the panel closed does nothing (it does not open it)
    await p().keyboard.press('Escape');
    assert.equal(await cityOpen(), false);
    // (2) opened with the keyboard, then focus moved INTO the panel: Escape closes and focus returns to the button
    await p().focus('[data-testid="city-button"]');
    await p().keyboard.press('Enter');
    await p().waitForSelector('[data-testid="city-controls"]');
    await p().focus('[data-testid="city-lighting-night"]');
    assert.notEqual(await focusedTestId(), 'city-button');
    await p().keyboard.press('Escape');
    assert.equal(await cityOpen(), false, 'Escape inside the panel closes it');
    assert.equal(await focusedTestId(), 'city-button', 'focus returns to the City button');
    // (3) the Close button does the same
    await p().keyboard.press('Enter');
    await p().waitForSelector('[data-testid="city-controls"]');
    await p().focus('[data-testid="city-controls-close"]');
    await p().keyboard.press('Enter');
    assert.equal(await cityOpen(), false, 'Close closes it');
    assert.equal(await focusedTestId(), 'city-button', 'and focus returns to the City button');
    // nothing about the city changed: the winter choice survived all of it
    assert.equal((readCityPresentation() as unknown as CityRecordLike).season.mode, 'manual');
  },
  STEPS_MS,
);

test(
  'TURNING REDUCED MOTION ON MID-FADE, in the real App, settles the season cross-fade and the weather fade at once (and turning it off restarts nothing)',
  async () => {
    await boot('no-preference'); // motion allowed: a season change cross-fades over 3 s, a weather change over 4 s
    await openCity();
    await choose('city-season-winter'); // a cross-fade begins
    await choose('city-weather-heavy-snow'); // and so does a weather fade
    const fades = () =>
      p().evaluate(() => {
        const city = (
          window as unknown as {
            __diHarnessOffice: {
              city: {
                seasons: { frame(): { season: string; from: string | null; t: number } | null };
                regional: { alpha(kind: string, slot: number): number };
              };
            };
          }
        ).__diHarnessOffice.city;
        return {
          season: city.seasons.frame(),
          heavy: [0, 1, 2, 3, 4].map((s) => city.regional.alpha('heavy-snow', s)),
        };
      });
    const mid = await fades();
    assert.equal(mid.season?.from, 'summer', 'the season cross-fade is running');
    assert.ok(mid.season!.t < 1 && mid.heavy.some((a) => a < 1), `both fades are in progress (${JSON.stringify(mid)})`);
    // the OS preference turns ON (the App's own media-query listener sets the flag): a couple of frames later both are settled
    await p().emulateMedia({ reducedMotion: 'reduce' });
    await settle(6);
    const settled = await fades();
    assert.deepEqual(settled.season, { season: 'winter', from: null, t: 1 }, 'the season is at its target');
    assert.deepEqual(settled.heavy, [1, 1, 1, 1, 1], 'every region shows heavy snow in full');
    // switching it back OFF restarts nothing
    await p().emulateMedia({ reducedMotion: 'no-preference' });
    await settle(30);
    assert.deepEqual(await fades(), settled);
  },
  STEPS_MS,
);
