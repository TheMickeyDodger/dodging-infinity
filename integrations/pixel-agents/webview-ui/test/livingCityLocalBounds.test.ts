/**
 * LIVING CITY 6–9: THE LOCAL BOUNDS. The declared bounds of the city live in constants.ts and are asserted by exact value beside
 * the module that uses them. A few constants live in the module that owns them (a convergence cap, a step cap, an encoding
 * stride, a trim length, thresholds); this file is where THOSE are pinned, in two halves each, the way every other bound is:
 *
 *   - the EXACT VALUE (the constant is exactly this);
 *   - the BEHAVIOUR (the engine honours it: a test drives the engine to the bound and to just past it).
 *
 * And it SWEEPS, so that the set is not whatever the author remembered: every module-level numeric constant declared in the
 * living-city modules is found mechanically and must be in the pin table below, and every pin must still exist.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import * as presentationCore from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import * as cityMask from '../src/office/city/cityMask.ts';
import * as cityRegions from '../src/office/city/cityRegions.ts';
import * as cityRegionWeather from '../src/office/city/cityRegionWeather.ts';
import * as cityWeather from '../src/office/city/cityWeather.ts';
import * as trafficBuild from '../src/office/traffic/trafficBuild.ts';
import { MAX_PASSES, planTraffic } from '../src/office/traffic/trafficBuild.ts';
import * as trafficGraph from '../src/office/traffic/trafficGraph.ts';
import {
  buildTrafficGraph,
  HEADING_E,
  indexOf,
  KIND_ROAD,
  LANE_STRIDE,
  laneOf,
} from '../src/office/traffic/trafficGraph.ts';
import * as trafficWorld from '../src/office/traffic/trafficWorld.ts';
import {
  MAX_STEP_SEC,
  NO_PEOPLE,
  ROUTE_TRIM_TILES,
  TrafficWorld,
} from '../src/office/traffic/trafficWorld.ts';
import { TileType } from '../src/office/types.ts';
import { PLUS_ROWS, plusGraph, plusTiles, tileOf } from './helpers/plusWorld.ts';

const SRC = path.join(__dirname, '..', 'src');
const CORE = path.join(__dirname, '..', '..', 'core', 'src');
const SERVER = path.join(__dirname, '..', '..', 'server', 'src');

// ── the pins, and the sweep that makes the set complete ───────────────────────────────────────────

/** module (relative to its root) -> { module namespace, root, every module-level numeric constant it declares, exactly }. */
const PINNED: Array<{
  file: string;
  root: string;
  ns: Record<string, unknown> | null;
  pins: Record<string, number>;
}> = [
  { file: 'office/traffic/trafficBuild.ts', root: SRC, ns: trafficBuild, pins: { MAX_PASSES: 5 } },
  {
    file: 'office/traffic/trafficGraph.ts',
    root: SRC,
    ns: trafficGraph,
    pins: {
      HEADING_E: 0,
      HEADING_S: 1,
      HEADING_W: 2,
      HEADING_N: 3,
      KIND_ROAD: 0,
      KIND_ZEBRA: 1,
      KIND_BOX: 2,
      LANE_STRIDE: 1024,
    },
  },
  {
    file: 'office/traffic/trafficWorld.ts',
    root: SRC,
    ns: trafficWorld,
    pins: {
      MAX_STEP_SEC: 0.25,
      RETARGET_SEC: 3,
      STOPPED_SPEED: 0.25,
      EPS: 0.001,
      YIELD_FADE_FACTOR: 4,
      ROUTE_TRIM_TILES: 16,
    },
  },
  { file: 'office/traffic/trafficRender.ts', root: SRC, ns: null, pins: {} },
  {
    file: 'office/city/cityMask.ts',
    root: SRC,
    ns: cityMask,
    pins: { MASK_NONE: 0, MASK_OUTDOOR: 1, MASK_INDOOR: 2 },
  },
  {
    file: 'office/city/cityWeather.ts',
    root: SRC,
    ns: cityWeather,
    pins: { SNOW_PHASE_RATE: 1.7 },
  },
  { file: 'office/city/cityLight.ts', root: SRC, ns: null, pins: {} },
  { file: 'office/city/cityEffects.ts', root: SRC, ns: null, pins: {} },
  // item 18 (seasons and regional weather): the module-level numeric constants of its modules
  { file: 'office/city/citySeason.ts', root: SRC, ns: null, pins: {} },
  { file: 'office/city/cityPersist.ts', root: SRC, ns: null, pins: {} },
  { file: 'office/city/citySeasonArt.ts', root: SRC, ns: null, pins: {} },
  {
    file: 'office/city/cityRegions.ts',
    root: SRC,
    ns: cityRegions,
    pins: {
      REGION_CITY: 0,
      REGION_COAST: 1,
      REGION_WOODLAND: 2,
      REGION_HIGHLANDS: 3,
      REGION_FARMLAND: 4,
      REGION_NONE: 255,
    },
  },
  {
    file: 'office/city/cityRegionWeather.ts',
    root: SRC,
    ns: cityRegionWeather,
    pins: { WEATHER_DRAW_WARMUP: 3 },
  },
  { file: 'office/city/cityRender.ts', root: SRC, ns: null, pins: {} },
  { file: 'office/livingLayer.ts', root: SRC, ns: null, pins: {} },
  { file: 'components/CityControls.tsx', root: SRC, ns: null, pins: {} },
  { file: 'hooks/useCityPresentation.ts', root: SRC, ns: null, pins: {} },
  {
    file: 'cityPresentation.ts',
    root: CORE,
    ns: presentationCore,
    pins: {
      CITY_PRESENTATION_VERSION: 1,
      CITY_PRESENTATION_MAX_BYTES: 256,
      // contract v2 (item 18)
      CITY_PRESENTATION_V2_VERSION: 2,
      CITY_PRESENTATION_V2_MAX_BYTES: 256,
      CITY_PRESENTATION_REGION_SLOTS: 5,
      CITY_PRESENTATION_SEASON_REMAINING_MAX_MS: 1_800_000,
      CITY_PRESENTATION_REGION_REMAINING_MAX_MS: 300_000,
      CITY_PRESENTATION_WET_STREAK_MAX: 2,
      CITY_PRESENTATION_STEP_MAX: 999_999,
      CITY_PRESENTATION_SEED_MAX: 4_294_967_295,
      CITY_PRESENTATION_DEFAULT_SEED: 0x51a7,
    },
  },
  { file: 'di/cityPresentationPersistence.ts', root: SERVER, ns: null, pins: {} },
];

/** Every `const NAME = <number>;` at the start of a line (module level), exported or not. */
const NUMERIC_CONST =
  /^(?:export )?const ([A-Za-z0-9_]+)(?:: [^=\n]+)? = (-?(?:0x[0-9a-fA-F]+|[0-9][0-9_]*(?:\.[0-9]+)?(?:e-?[0-9]+)?));/gm;

test('SWEEP: every module-level numeric constant in the living-city modules is pinned here, exactly, and every pin still exists', () => {
  const swept: string[] = [];
  for (const { file, root, ns, pins } of PINNED) {
    const source = fs.readFileSync(path.join(root, file), 'utf-8');
    const found = new Map<string, number>();
    for (const m of source.matchAll(NUMERIC_CONST)) found.set(m[1], Number(m[2].replace(/_/g, '')));
    // the set the module declares is the set pinned: nothing unpinned, nothing stale
    assert.deepEqual(
      [...found.keys()].sort(),
      Object.keys(pins).sort(),
      `${file}: declared constants against pinned`,
    );
    for (const [name, value] of Object.entries(pins)) {
      assert.equal(found.get(name), value, `${file}: ${name} is declared as ${value}`);
      // and the constant the engine actually uses is that value: its export is read, not assumed
      assert.ok(ns, `${file}: pinned constants need the module to be loaded`);
      assert.equal(ns[name], value, `${file}: ${name} exports ${value}`);
      swept.push(`${name}=${value}`);
    }
  }
  console.log(
    `MEASURED sweep: ${PINNED.length} modules, ${swept.length} module-level numeric constants, each pinned: ${swept.join(' ')}`,
  );
});

test('the City panel keeps its refresh cadence in constants.ts, by exact value', () => {
  assert.equal(C.CITY_STATUS_REFRESH_MS, 1000);
});

/** ITEM 18: every numeric bound the seasons and the regional weather added to constants.ts, by exact value. */
const ITEM18_CONSTANTS: Record<string, number> = {
  CITY_SEASON_INTERVAL_MS: 1_800_000,
  CITY_CLOCK_MAX_STEP_SEC: 0.25,
  CITY_SEASON_FADE_MS: 3_000,
  CITY_SEASON_CACHE_MAX_ENTRIES: 2_048,
  CITY_REGION_SLOTS: 5,
  CITY_REGION_FALLBACK_BANDS: 3,
  CITY_REGION_BAND_MIN_COLS: 96,
  CITY_WEATHER_INTERVAL_MIN_MS: 120_000,
  CITY_WEATHER_INTERVAL_MAX_MS: 300_000,
  CITY_WEATHER_FADE_MS: 4_000,
  CITY_WEATHER_SCHEDULE_SEED: 0x51a7,
  CITY_WEATHER_MAX_WET_DRAWS: 2,
  CITY_WEATHER_STEP_LIMIT: 1_000_000,
  CITY_SAVE_POLL_MS: 1_000,
  CITY_SAVE_MIN_GAP_MS: 2_000,
  CITY_SAVE_CHECKPOINT_MS: 60_000,
  CITY_HEAVY_SNOW_PARTICLES: 520,
  CITY_WEATHER_PARTICLE_TOTAL: 520,
  CITY_WEATHER_BALANCE_RATE: 200,
  CITY_REGION_EDGE_TILES: 4,
  CITY_SNOW_HALO_PX: 1,
  CITY_SNOW_FLAKE_SCALE: 3,
  CITY_HEAVY_SNOW_SPEED: 0.16,
  CITY_HEAVY_SNOW_SWAY: 0.03,
  CITY_HEAVY_SNOW_SIZE_PX: 2,
  CITY_HEAVY_SNOW_NEAR_DEPTH: 0.75,
  CITY_TEST_CLOCK_MAX_SECONDS: 14_400,
  CITY_SEASON_GREEN_HUE_MIN: 60,
  CITY_SEASON_GREEN_HUE_MAX: 175,
  CITY_SEASON_GREY_SATURATION: 0.1,
  CITY_SEASON_TRUNK_LIGHTNESS: 0.32,
};

test('SWEEP (item 18): every numeric constant of the seasons and regional weather section of constants.ts is pinned here, exactly, and every pin still exists', () => {
  const source = fs.readFileSync(path.join(SRC, 'constants.ts'), 'utf-8');
  const start = source.indexOf('// ── Living city: seasons and regional weather');
  assert.ok(start >= 0, 'the section is present');
  const end = source.indexOf('// ── ', start + 10);
  const section = source.slice(start, end < 0 ? undefined : end);
  const found = new Map<string, number>();
  for (const m of section.matchAll(NUMERIC_CONST)) found.set(m[1], Number(m[2].replace(/_/g, '')));
  assert.deepEqual([...found.keys()].sort(), Object.keys(ITEM18_CONSTANTS).sort(), 'declared against pinned');
  for (const [name, value] of Object.entries(ITEM18_CONSTANTS)) {
    assert.equal(found.get(name), value, `${name} is declared as ${value}`);
    assert.equal((C as Record<string, unknown>)[name], value, `${name} exports ${value}`);
  }
  console.log(
    `MEASURED sweep (item 18): ${found.size} numeric constants in constants.ts, each pinned: ${[...found].map(([n, v]) => `${n}=${v}`).join(' ')}`,
  );
});

// ── MAX_PASSES: the convergence cap is honoured ───────────────────────────────────────────────────

/**
 * ONE taxi on the east lane of the plus world, planned with an entry function whose answer for the taxi's length follows
 * `schedule` by the number of graph builds so far (true: a length it can drive with; false: one it cannot). That is a world
 * whose answer changes from pass to pass, which is the only way a fixed point can fail to settle, so the plan's own cap has to
 * stop it. The plan's pass n compares the homes of build n with those of build n-1 (pass 1: with every vehicle piece).
 */
function plan(schedule: readonly boolean[]) {
  let builds = 0;
  const valid = () =>
    schedule[Math.min(builds, schedule.length) - 1] ?? schedule[schedule.length - 1];
  const result = planTraffic(
    {
      cols: 24,
      rows: PLUS_ROWS,
      tiles: plusTiles(),
      furniture: [{ uid: 'taxi', type: 'VEHICLE_TAXI_RIGHT', col: 3, row: 13 }],
    },
    () => ({ footprintW: valid() ? 3 : 9, footprintH: 1 }),
    () => {
      builds++;
      return new Set<string>();
    },
  );
  return { ...result, builds };
}

test('MAX_PASSES: a plan that settles on the first pass is accepted, and costs one graph', () => {
  assert.equal(MAX_PASSES, 5);
  const r = plan([true]);
  assert.deepEqual([r.settled, r.passes, r.homes.length, r.builds], [true, 1, 1, 1]);
});

test('MAX_PASSES: a plan that settles on pass FIVE is accepted, with its homes', () => {
  // homes by build: none, taxi, none, taxi, taxi  ->  pass 5 is the first whose answer matches the one before
  const r = plan([false, true, false, true, true]);
  assert.deepEqual([r.settled, r.passes, r.homes.length, r.builds], [true, 5, 1, 5]);
});

test('MAX_PASSES: a plan that would settle on pass SIX is refused: five passes, no animated vehicle, exactly seven graphs', () => {
  // homes by build: none, taxi, none, taxi, none, none  ->  pass 6 would settle; the cap is five
  const r = plan([false, true, false, true, false, false]);
  assert.deepEqual([r.settled, r.passes, r.homes.length], [false, MAX_PASSES, 0]);
  // the initial graph, one per pass that did not settle (five), and the last one without any animated vehicle
  assert.equal(
    r.builds,
    1 + MAX_PASSES + 1,
    'the cap is honoured: neither fewer nor more passes were made',
  );
});

test('MAX_PASSES: a world whose answer never stops changing is refused the same way', () => {
  const r = plan(Array.from({ length: 12 }, (_, i) => i % 2 === 1));
  assert.deepEqual(
    [r.settled, r.passes, r.homes.length, r.builds],
    [false, MAX_PASSES, 0, 1 + MAX_PASSES + 1],
  );
  // and the shipped roads settle long before the cap (judged on the shipped world in trafficWorld.test.ts)
});

// ── MAX_STEP_SEC: one oversized step moves a vehicle by one capped step ───────────────────────────

function drivingWorld(): TrafficWorld {
  const g = plusGraph();
  const code = g.laneAt(tileOf(5, 13), HEADING_E);
  const w = new TrafficWorld(g, [
    {
      uid: 'taxi',
      kind: 'TAXI',
      heading: HEADING_E,
      lane: laneOf(code),
      idx: indexOf(code),
      body: 3,
    },
  ]);
  w.setCap(1);
  for (let k = 0; k < 40; k++) w.update(0.05, NO_PEOPLE, false); // two seconds: out of its stagger and under way
  return w;
}
const pose = (w: TrafficWorld) => {
  const v = w.vehicles[0];
  return { state: v.state, hx: v.hx, hy: v.hy, v: v.v, head: v.head };
};

test('MAX_STEP_SEC: a step longer than the cap is cut to the cap, and a step under it is not', () => {
  assert.equal(MAX_STEP_SEC, 0.25);
  const before = pose(drivingWorld());
  assert.equal(before.state, 'drive', 'the vehicle is driving before the step');
  const after = (dt: number) => {
    const w = drivingWorld();
    w.update(dt, NO_PEOPLE, false);
    return pose(w);
  };
  const at = after(MAX_STEP_SEC);
  // an oversized step (ten seconds, and just over the cap) moves the vehicle exactly as the capped step does
  assert.deepEqual(after(10), at, 'a 10 s step is one 0.25 s step');
  assert.deepEqual(after(MAX_STEP_SEC + 0.01), at, 'just over the cap is the cap');
  // and the cap is exactly 0.25: a step just under it, and a short one, are different, shorter steps
  assert.notDeepEqual(after(MAX_STEP_SEC - 0.01), at);
  assert.notDeepEqual(after(0.1), at);
  // the distance a vehicle can cover in one call is bounded by its fastest speed over the cap (plus what it can gain)
  const moved = Math.abs(after(10).hx - before.hx);
  assert.ok(
    moved > 0 &&
      moved <=
        C.TRAFFIC_SPEED_MAX * MAX_STEP_SEC + 0.5 * C.TRAFFIC_ACCEL * MAX_STEP_SEC ** 2 + 1e-9,
    `moved ${moved} tiles in a 10 s call`,
  );
});

// ── LANE_STRIDE: a lane tile's code always decodes, at the largest layout ─────────────────────────

test('LANE_STRIDE: the coding of a lane tile holds at the largest lane the layout bound allows', () => {
  assert.equal(LANE_STRIDE, 1024);
  const COLS = 384; // LAYOUT_MAX_DIM
  const ROWS = 8;
  const tiles = new Array<number>(COLS * ROWS).fill(TileType.VOID);
  for (let c = 0; c < COLS; c++) {
    tiles[2 * COLS + c] = TileType.FLOOR_15;
    tiles[3 * COLS + c] = TileType.FLOOR_16;
    tiles[4 * COLS + c] = TileType.FLOOR_15;
  }
  const g = buildTrafficGraph(COLS, ROWS, tiles, () => false);
  const longest = Math.max(...g.lanes.map((l) => l.tiles.length));
  assert.equal(longest, COLS, 'a band across the widest layout is one lane of 384 tiles');
  assert.ok(longest < LANE_STRIDE, 'and its index fits the stride');
  const last = g.laneAt(4 * COLS + (COLS - 1), HEADING_E);
  assert.ok(last >= 0);
  assert.equal(indexOf(last), COLS - 1, 'the last tile decodes to its index');
  assert.equal(
    g.lanes[laneOf(last)].tiles[indexOf(last)],
    4 * COLS + COLS - 1,
    'and to its own tile',
  );
  assert.equal(g.lanes[laneOf(last)].kind[indexOf(last)], KIND_ROAD);
  // the greatest code the engine can make (the most lanes, the longest lane) is an integer well inside 32 bits
  const greatest = (C.TRAFFIC_MAX_LANES - 1) * LANE_STRIDE + (COLS - 1);
  assert.ok(Number.isSafeInteger(greatest) && greatest < 2 ** 31);
  assert.equal(laneOf(greatest), C.TRAFFIC_MAX_LANES - 1);
  assert.equal(indexOf(greatest), COLS - 1);
});

// ── ROUTE_TRIM_TILES: a route never keeps more than the trim length behind the body ───────────────

test('ROUTE_TRIM_TILES: over two simulated minutes no vehicle keeps more than the trim length of route behind its body', () => {
  assert.equal(ROUTE_TRIM_TILES, 16);
  const w = drivingWorld();
  let worstBehind = 0;
  let longestRoute = 0;
  for (let k = 0; k < 120 / 0.05; k++) {
    w.update(0.05, NO_PEOPLE, false);
    for (const v of w.vehicles) {
      // the tiles of route that lie behind the body: the part that `trim` drops once it reaches the trim length
      const behind = Math.max(0, Math.floor(v.head - (v.body - 1) - 0.5) + 1);
      worstBehind = Math.max(worstBehind, behind);
      longestRoute = Math.max(longestRoute, v.route.length);
    }
  }
  console.log(
    `MEASURED route trim over 120 s: the most route kept behind a body ${worstBehind} tiles (trim length ${ROUTE_TRIM_TILES}); the longest route held ${longestRoute} tiles (bound ${C.TRAFFIC_ROUTE_MAX_TILES})`,
  );
  assert.ok(worstBehind <= ROUTE_TRIM_TILES, `${worstBehind} tiles kept behind a body`);
  assert.ok(
    worstBehind >= ROUTE_TRIM_TILES - 4,
    'and the run reached the trim (the check is not vacuous)',
  );
  assert.ok(longestRoute <= C.TRAFFIC_ROUTE_MAX_TILES);
});
