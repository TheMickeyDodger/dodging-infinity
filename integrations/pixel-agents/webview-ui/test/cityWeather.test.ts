/**
 * LIVING CITY 6–9, weather and the effects state, and the city inside the REAL OfficeState on the SHIPPED
 * living metropolis. The property that matters most is the last group: choosing lighting and weather is
 * decoration, so two worlds stepped identically — one with night and rain, one without — must hold
 * exactly the same residents, trains and characters.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { CityPresentation } from '../../core/src/cityPresentation.ts';
import { cityRgba } from '../src/constants.ts';
import { CityEffects } from '../src/office/city/cityEffects.ts';
import * as M from '../src/office/city/cityMask.ts';
import * as R from '../src/office/city/cityRender.ts';
import { WeatherField, weatherVeil } from '../src/office/city/cityWeather.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const present = (over: Partial<CityPresentation>): CityPresentation => ({
  v: 1,
  lighting: 'day',
  weather: 'off',
  traffic: 'normal',
  ...over,
});

// ── the weather field ─────────────────────────────────────────────────────────

test('the field is a fixed size and a kind only chooses how many particles are live', () => {
  const f = new WeatherField();
  assert.equal(f.xs.length, 520);
  assert.equal(f.ys.length, 520);
  assert.equal(f.depth.length, 520);
  assert.equal(f.count, 0); // no weather: nothing live
  f.setKind('rain');
  assert.equal(f.count, 520);
  f.setKind('snow');
  assert.equal(f.count, 300);
  f.setKind('off');
  assert.equal(f.count, 0);
});

test('the field is deterministic: the same steps give the same particles', () => {
  const a = new WeatherField();
  const b = new WeatherField();
  a.setKind('rain');
  b.setKind('rain');
  for (let k = 0; k < 200; k++) {
    a.update(0.05);
    b.update(0.05);
  }
  assert.deepEqual([...a.xs], [...b.xs]);
  assert.deepEqual([...a.ys], [...b.ys]);
});

test('particles stay on the screen for ever and depth stays in range, however long it runs', () => {
  for (const kind of ['rain', 'snow'] as const) {
    const f = new WeatherField();
    f.setKind(kind);
    for (let k = 0; k < 4000; k++) f.update(0.1);
    for (let i = 0; i < f.count; i++) {
      assert.ok(f.xs[i] >= 0 && f.xs[i] < 1, `${kind} x ${f.xs[i]}`);
      assert.ok(f.ys[i] >= 0 && f.ys[i] < 1, `${kind} y ${f.ys[i]}`);
      assert.ok(f.depth[i] >= 0.5 && f.depth[i] < 1);
    }
    assert.equal(f.xs.length, 520); // nothing grew
  }
});

test('rain falls and drifts at the stated speeds; snow falls slowly and sways', () => {
  const rain = new WeatherField();
  rain.setKind('rain');
  const y0 = rain.ys[0];
  const x0 = rain.xs[0];
  const d = rain.depth[0];
  rain.update(0.1);
  const frac = (v: number) => v - Math.floor(v);
  assert.ok(Math.abs(rain.ys[0] - frac(y0 + 1.1 * d * 0.1)) < 1e-5);
  assert.ok(Math.abs(rain.xs[0] - frac(x0 + 0.12 * d * 0.1)) < 1e-5);
  const snow = new WeatherField();
  snow.setKind('snow');
  const sy = snow.ys[0];
  snow.update(0.1);
  assert.ok(Math.abs(snow.ys[0] - frac(sy + 0.07 * snow.depth[0] * 0.1)) < 1e-5);
});

test('no weather and no time change nothing', () => {
  const f = new WeatherField();
  const before = [...f.ys];
  f.update(1); // kind off
  assert.deepEqual([...f.ys], before);
  f.setKind('rain');
  f.update(0);
  f.update(-1);
  assert.deepEqual([...f.ys], before);
});

// ── the effects state ─────────────────────────────────────────────────────────

test('the effects state fades to its target, jumps under reduced motion, and moves weather only when motion is allowed', () => {
  const noon = new Date(2026, 9, 20, 12, 0, 0).getTime();
  const e = new CityEffects();
  assert.equal(e.level, 0);
  e.setPresentation(present({ lighting: 'night', weather: 'rain' }), noon, false);
  assert.equal(e.level, 0); // set, not yet shown: it fades in
  e.update(0.4, noon, false);
  assert.equal(e.level, 0.5);
  e.update(0.4, noon, false);
  assert.equal(e.level, 1);
  assert.equal(e.weather.kind, 'rain');

  const still = new CityEffects();
  still.setPresentation(present({ lighting: 'night', weather: 'rain' }), noon, true);
  assert.equal(still.level, 1); // reduced motion: at once
  const frozen = [...still.weather.ys];
  still.update(1, noon, true);
  assert.deepEqual([...still.weather.ys], frozen); // reduced motion: one still frame
  assert.equal(still.level, 1);
});

test('the clock mode follows the clock it is handed, and manual modes ignore it', () => {
  const e = new CityEffects();
  const midnight = new Date(2026, 9, 20, 0, 30, 0).getTime();
  const noon = new Date(2026, 9, 20, 12, 0, 0).getTime();
  e.setPresentation(present({ lighting: 'clock' }), noon, true);
  assert.equal(e.level, 0);
  e.update(0, midnight, true);
  assert.equal(e.level, 1);
  e.setPresentation(present({ lighting: 'day' }), midnight, true);
  assert.equal(e.level, 0);
  assert.match(e.lightingStatus(midnight), /as it always looked/);
});

test('the effects state copies what it is given', () => {
  const e = new CityEffects();
  const given = present({ weather: 'snow' });
  e.setPresentation(given, 0, true);
  given.weather = 'rain';
  assert.equal(e.presentation.weather, 'snow');
});

// ── the weather render pass, against a recording canvas ───────────────────────

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  fill: unknown;
}
class FakeCtx implements R.CityCtx {
  globalCompositeOperation = 'source-over';
  globalAlpha = 1;
  fillStyle: unknown = '';
  imageSmoothingEnabled = false;
  rects: Rect[] = [];
  depth = 0;
  private stack: unknown[] = [];
  save() {
    this.depth++;
    this.stack.push(this.fillStyle);
  }
  restore() {
    this.depth--;
    this.fillStyle = this.stack.pop();
  }
  clearRect() {}
  fillRect(x: number, y: number, w: number, h: number) {
    this.rects.push({ x, y, w, h, fill: this.fillStyle });
  }
  drawImage() {}
  createRadialGradient() {
    return { addColorStop: () => undefined };
  }
}
const W = 800;
const H = 600;
const frame: R.CityFrame = {
  canvasWidth: W,
  canvasHeight: H,
  offsetX: 0,
  offsetY: 0,
  zoom: 2,
  view: { c0: 0, c1: 26, r0: 0, r1: 20 },
};
const COLS = 26;
const ROWS = 20;
const maskOf = (indoorWhen: (c: number, r: number) => boolean): M.CityMask => {
  const tiles: number[] = [];
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++)
      tiles.push(indoorWhen(c, r) ? TileType.FLOOR_26 : TileType.FLOOR_15);
  return M.buildCityMask(COLS, ROWS, tiles);
};

test('rain draws every particle over open ground, in the rain colour', () => {
  const f = new WeatherField();
  f.setKind('rain');
  const ctx = new FakeCtx();
  assert.equal(
    R.renderCityWeather(
      ctx,
      f,
      maskOf(() => false),
      frame,
    ),
    520,
  );
  assert.equal(ctx.rects.length, 520);
  // one sprite pixel wide (2 device pixels at zoom 2), 3 to 6 sprite pixels long
  assert.ok(
    ctx.rects.every(
      (r) => r.fill === cityRgba(184, 214, 255, 0.72) && r.w === 2 && r.h >= 6 && r.h <= 12,
    ),
  );
  assert.equal(ctx.depth, 0);
});

test('snow draws its own, smaller field in the snow colour', () => {
  const f = new WeatherField();
  f.setKind('snow');
  const ctx = new FakeCtx();
  assert.equal(
    R.renderCityWeather(
      ctx,
      f,
      maskOf(() => false),
      frame,
    ),
    300,
  );
  assert.ok(
    ctx.rects.every((r) => r.fill === cityRgba(245, 248, 255, 0.9) && r.w === r.h && r.w >= 1),
  );
});

test('each kind lays its own veil over the outdoors, and clear skies lay none', () => {
  assert.deepEqual(weatherVeil('rain'), { r: 62, g: 78, b: 104, a: 0.2 });
  assert.deepEqual(weatherVeil('snow'), { r: 120, g: 134, b: 160, a: 0.14 });
  assert.equal(weatherVeil('off'), null);
});

test('indoors stays dry: no particle is drawn over an indoor tile, and none over nothing', () => {
  const f = new WeatherField();
  f.setKind('rain');
  // all indoors: nothing is drawn
  const none = new FakeCtx();
  assert.equal(
    R.renderCityWeather(
      none,
      f,
      maskOf(() => true),
      frame,
    ),
    0,
  );
  assert.equal(none.rects.length, 0);
  // the left half indoors: only particles over the right half are drawn, and each is over outdoor ground
  const mask = maskOf((c) => c < 13);
  const ctx = new FakeCtx();
  const drawn = R.renderCityWeather(ctx, f, mask, frame);
  assert.ok(drawn > 0 && drawn < 520);
  for (const r of ctx.rects)
    assert.equal(M.maskAt(mask, Math.floor(r.x / 32), Math.floor(r.y / 32)), M.MASK_OUTDOOR);
  // off the map is nothing: a mask smaller than the canvas draws only over the map
  const small = new FakeCtx();
  const partial = R.renderCityWeather(
    small,
    f,
    M.buildCityMask(5, 5, new Array(25).fill(TileType.FLOOR_15)),
    frame,
  );
  assert.ok(partial < 520);
  // clear skies draw nothing at all
  const clear = new WeatherField();
  const idle = new FakeCtx();
  assert.equal(
    R.renderCityWeather(
      idle,
      clear,
      maskOf(() => false),
      frame,
    ),
    0,
  );
  assert.equal(idle.depth, 0);
});

// ── the city inside the real OfficeState, on the shipped metropolis ───────────

let layout: OfficeLayout;
beforeAll(() => {
  loadBundledCatalog();
  layout = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
}, 120_000);

const newOffice = () => {
  const os = new OfficeState(structuredClone(layout));
  os.setAmbientEnabled(true);
  return os;
};

test('a clear day costs nothing: there is no render input at all', () => {
  const os = newOffice();
  assert.equal(os.cityRenderInput(), null);
  os.setCityPresentation(present({ lighting: 'day', weather: 'off' }));
  os.update(1);
  assert.equal(os.cityRenderInput(), null);
}, 60_000);

test("night gives the renderer the mask, the world's lamps and a light for every real agent", () => {
  const os = newOffice();
  os.addAgent(1, undefined, undefined, undefined, true);
  os.addAgent(2, undefined, undefined, undefined, true);
  os.setCityPresentation(present({ lighting: 'night' }));
  os.update(1); // fades in over 0.8 s
  const input = os.cityRenderInput()!;
  assert.equal(input.level, 1);
  assert.equal(input.weather, null);
  assert.equal(input.veil, null); // night without weather has no veil
  assert.equal(input.mask, M.cityMaskFor(os.getLayout()));
  assert.equal(input.lamps.length, 110 + 32 + 6);
  const chars = os.getCharacters();
  assert.equal(input.agents.length, chars.length);
  assert.deepEqual(input.agents[0], { x: chars[0].x, y: chars[0].y - 4 });
  // a character that is despawning is no longer lit
  chars[0].matrixEffect = 'despawn';
  assert.equal(os.cityRenderInput()!.agents.length, chars.length - 1);
}, 60_000);

test('rain alone gives weather and its veil but no night: agents keep their light, the lamps stay off', () => {
  const os = newOffice();
  os.addAgent(1, undefined, undefined, undefined, true);
  os.setCityPresentation(present({ weather: 'rain' }));
  os.update(0.1);
  const input = os.cityRenderInput()!;
  assert.equal(input.level, 0); // daytime
  assert.equal(input.weather, os.city.weather);
  assert.equal(input.weather!.count, 520);
  assert.deepEqual(input.veil, { r: 62, g: 78, b: 104, a: 0.2 });
  assert.equal(input.agents.length, 1); // a real agent is never dimmed by the veil
  assert.deepEqual([...input.lamps], []); // lamps are a night thing, never a daytime shower
  // clear skies at day: nothing at all, and no veil
  os.setCityPresentation(present({ weather: 'off' }));
  assert.equal(os.cityRenderInput(), null);
  // snow lays the snow veil
  os.setCityPresentation(present({ weather: 'snow' }));
  assert.deepEqual(os.cityRenderInput()!.veil, { r: 120, g: 134, b: 160, a: 0.14 });
}, 60_000);

test('reduced motion: the stage shows at once and the rain is one still frame', () => {
  const os = newOffice();
  os.reducedMotion = true;
  os.setCityPresentation(present({ lighting: 'dusk', weather: 'snow' }));
  assert.equal(os.city.level, 0.5); // no fade
  const frozen = [...os.city.weather.ys];
  os.update(2);
  assert.deepEqual([...os.city.weather.ys], frozen);
  assert.equal(os.cityRenderInput()!.level, 0.5);
}, 60_000);

test('lighting and weather are decoration: identical worlds stay identical whatever the city looks like', () => {
  const plain = newOffice();
  const styled = newOffice();
  // palette, hue and seat are PINNED: left to themselves they are chosen at random, which would differ
  // between any two worlds, city or no city
  const seat = [...plain.seats.keys()][0];
  for (const o of [plain, styled]) o.addAgent(1, 2, 0, seat, true);
  styled.setCityPresentation(present({ lighting: 'night', weather: 'rain', traffic: 'low' }));
  const DT = 0.1;
  for (let k = 0; k < 600; k++) {
    plain.update(DT);
    styled.update(DT);
  }
  // residents and trains: the saved snapshot is the whole of their state, and it is byte-identical
  assert.equal(
    JSON.stringify(styled.serializeAmbient(0)),
    JSON.stringify(plain.serializeAmbient(0)),
  );
  assert.equal(JSON.stringify(styled.getTrains()), JSON.stringify(plain.getTrains()));
  // real characters: same identity, assigned seat, activity and bubble. Their POSITION is deliberately not
  // compared: a real character's idle wander is not seeded (two plain worlds with no city at all end up in
  // different places), so position says nothing about what the city did. The seeded simulation above does.
  const key = (o: OfficeState) =>
    JSON.stringify(
      o
        .getCharacters()
        .map((c) => [
          c.id,
          c.palette,
          c.hueShift,
          c.seatId,
          c.isActive,
          c.bubbleType,
          c.currentTool,
          c.isSubagent,
        ]),
    );
  assert.equal(key(styled), key(plain));
  assert.equal(styled.getCharacters().length, 1);
  // and the city really was different: the styled world draws night and rain, the plain one nothing
  assert.equal(plain.cityRenderInput(), null);
  assert.equal(styled.cityRenderInput()!.level, 1);
}, 120_000);
