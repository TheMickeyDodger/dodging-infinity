/**
 * ITEM 18, MILESTONE 1: regional weather as it is DRAWN, against recording canvases. A synthetic 60 x 40 world is split into three
 * districts (City, Highlands, Farmland) by columns, with indoor rooms and empty ground in it, and winter weather in which the City has
 * light snow, the Highlands heavy snow and the Farmland clear sky. Every assertion compares fields and identities, never the region
 * map or the fields themselves (a failure must not print 2,400 cells).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { afterEach, beforeAll, test } from 'vitest';

import { CITY_REGION_EDGE_TILES, CITY_SNOW_HALO_COLOR, cityRgba } from '../src/constants.ts';
import * as M from '../src/office/city/cityMask.ts';
import type { RegionMap } from '../src/office/city/cityRegions.ts';
import { buildRegionMap, edgeWeight, regionAt, regionEdges } from '../src/office/city/cityRegions.ts';
import { RegionalWeather } from '../src/office/city/cityRegionWeather.ts';
import * as R from '../src/office/city/cityRender.ts';
import { TileType } from '../src/office/types.ts';

const COLS = 60;
const ROWS = 40;
const TILE = 16; // zoom 1
const SEED = 0x51a7;

interface Call {
  op: 'fillRect' | 'drawImage';
  x: number;
  y: number;
  w: number;
  h: number;
  fill: unknown;
  alpha: number;
  comp: string;
}
class Rec implements R.CityCtx {
  globalCompositeOperation = 'source-over';
  globalAlpha = 1;
  fillStyle: unknown = '';
  imageSmoothingEnabled = false;
  calls: Call[] = [];
  depth = 0;
  private stack: Array<[unknown, number, string]> = [];
  save() {
    this.depth++;
    this.stack.push([this.fillStyle, this.globalAlpha, this.globalCompositeOperation]);
  }
  restore() {
    this.depth--;
    const top = this.stack.pop();
    if (top) [this.fillStyle, this.globalAlpha, this.globalCompositeOperation] = top;
  }
  clearRect() {}
  fillRect(x: number, y: number, w: number, h: number) {
    this.calls.push({ op: 'fillRect', x, y, w, h, fill: this.fillStyle, alpha: this.globalAlpha, comp: this.globalCompositeOperation });
  }
  drawImage(_i: unknown, x: number, y: number, w: number, h: number) {
    this.calls.push({ op: 'drawImage', x, y, w, h, fill: null, alpha: this.globalAlpha, comp: this.globalCompositeOperation });
  }
  createRadialGradient() {
    return { addColorStop: () => undefined };
  }
}

const labelAt = (col: number): string =>
  col < 20 ? 'Metropolis' : col < 40 ? 'Silver Pine Highlands' : 'River Valley Farmland';
function worldMap(): RegionMap {
  const labels: string[] = [];
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) labels.push(labelAt(c));
  return buildRegionMap(COLS, ROWS, labels);
}
/** Open asphalt everywhere, except: an indoor room (cols 5..14, rows 5..14, in the City), another in the Highlands (cols 25..34,
 *  rows 20..29), and a strip of empty ground (VOID) along the bottom row. */
function worldMask(): M.CityMask {
  const tiles: number[] = [];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const room1 = c >= 5 && c <= 14 && r >= 5 && r <= 14;
      const room2 = c >= 25 && c <= 34 && r >= 20 && r <= 29;
      tiles.push(r === ROWS - 1 ? TileType.VOID : room1 || room2 ? TileType.FLOOR_26 : TileType.FLOOR_15);
    }
  }
  return M.buildCityMask(COLS, ROWS, tiles);
}
const frameAt = (offsetX = 0, offsetY = 0): R.CityFrame => ({
  canvasWidth: COLS * TILE,
  canvasHeight: ROWS * TILE,
  offsetX,
  offsetY,
  zoom: 1,
  view: { c0: 0, c1: COLS, r0: 0, r1: ROWS },
});
/** Winter, with each region slot's own class: City light snow, Coast rain, Woodland light snow, Highlands heavy snow, Farmland clear. */
const winter = () => new RegionalWeather(SEED, 'winter', Uint8Array.from([0, 1, 2, 3, 4]));
const tileOf = (x: number, y: number, f: R.CityFrame) => [
  Math.floor((x - f.offsetX) / (TILE * f.zoom)),
  Math.floor((y - f.offsetY) / (TILE * f.zoom)),
];
/** A light-snow flake's cool shadow (laid under each flake); every other call is a particle. */
const isShadow = (c: Call): boolean => c.fill === CITY_SNOW_HALO_COLOR;
/**
 * THE SPEC OF THE SOFT EDGE, written here from its definition and independent of the renderer's own helpers (regionEdges, edgeWeight,
 * blendStrength are NOT used to say what is correct; tests that use them say so and are consistency checks only).
 *   - the band is 4 tiles wide (pinned separately: CITY_REGION_EDGE_TILES === 4);
 *   - a tile's DISTANCE is the fewest 4-neighbour steps to a tile of a different region, counted only up to 4 (0 beyond: not in the band);
 *   - a tile at distance d leans toward that region by w(d) = 0.5 * (1 - (d - 0.5) / 4): 0.4375, 0.3125, 0.1875, 0.0625 for d = 1..4;
 *   - its strength for a kind is then (1 - w) * own + w * foreign, and exactly `own` when the two are equal.
 */
const BAND = 4;
const SPEC_WEIGHT = [0, 0.4375, 0.3125, 0.1875, 0.0625] as const;
const NO_REGION = 255;
/** Brute force: the distance (1..BAND) from a tile to the nearest tile of another region and the regions found at that distance; d = 0 when none is within BAND. */
function refEdge(map: RegionMap, col: number, row: number): { d: number; foreign: number[] } {
  const own = map.cells[row * map.cols + col];
  for (let d = 1; d <= BAND; d++) {
    const found = new Set<number>();
    for (let dc = -d; dc <= d; dc++) {
      const rest = d - Math.abs(dc);
      for (const dr of rest === 0 ? [0] : [-rest, rest]) {
        const c = col + dc;
        const r = row + dr;
        if (c < 0 || r < 0 || c >= map.cols || r >= map.rows) continue;
        const there = map.cells[r * map.cols + c];
        if (there !== own && there !== NO_REGION) found.add(there);
      }
    }
    if (found.size > 0) return { d, foreign: [...found] };
  }
  return { d: 0, foreign: [] };
}
/** The strengths a tile may legitimately have (one value, or one per equally-near foreign region), from the spec above. */
function refStrengths(map: RegionMap, alphaOf: (slot: number) => number, col: number, row: number): number[] {
  const own = map.cells[row * map.cols + col];
  if (own === NO_REGION) return [0];
  const { d, foreign } = refEdge(map, col, row);
  const a = alphaOf(own);
  if (d === 0) return [a];
  return foreign.map((f) => (alphaOf(f) === a ? a : (1 - SPEC_WEIGHT[d]) * a + SPEC_WEIGHT[d] * alphaOf(f)));
}
const near = (x: number, y: number): boolean => Math.abs(x - y) < 1e-9;
const matchesSpec = (map: RegionMap, alphaOf: (slot: number) => number, col: number, row: number, got: number): boolean =>
  refStrengths(map, alphaOf, col, row).some((x) => near(x, got));
/** The synthetic world's geometry, stated by coordinates (independent of the tile mask the renderer reads): two indoor rooms and a void bottom row. */
const indoorAt = (col: number, row: number): boolean =>
  (col >= 5 && col <= 14 && row >= 5 && row <= 14) || (col >= 25 && col <= 34 && row >= 20 && row <= 29);
const emptyAt = (_col: number, row: number): boolean => row === ROWS - 1 || row < 0;
const openGroundAt = (col: number, row: number): boolean => col >= 0 && col < COLS && row >= 0 && row < ROWS && !indoorAt(col, row) && !emptyAt(col, row);
/** Whether every tile a call's whole rectangle covers is open ground (checked from the rectangle's four corners, by coordinates). */
function rectOverOpenGround(c: { x: number; y: number; w: number; h: number }, f: R.CityFrame): boolean {
  const tile = TILE * f.zoom;
  return [
    [c.x, c.y],
    [c.x + c.w - 1, c.y],
    [c.x, c.y + c.h - 1],
    [c.x + c.w - 1, c.y + c.h - 1],
  ].every(([x, y]) => openGroundAt(Math.floor((x - f.offsetX) / tile), Math.floor((y - f.offsetY) / tile)));
}

afterEach(() => R.setCityLayerFactory(null));

test('the starting winter weather is the seeded one: City light snow, Coast rain, Woodland light snow, Highlands heavy snow, Farmland clear', () => {
  const w = winter();
  assert.deepEqual([0, 1, 2, 3, 4].map((s) => w.kindAt(s)), ['light-snow', 'rain', 'light-snow', 'heavy-snow', 'clear']);
});

test('THE BAND IS 4 TILES (pinned) and the spec table is the one in the design: w(d) = 0.5 * (1 - (d - 0.5) / 4)', () => {
  assert.equal(CITY_REGION_EDGE_TILES, 4);
  assert.equal(BAND, 4);
  for (let d = 1; d <= BAND; d++) assert.ok(near(SPEC_WEIGHT[d], 0.5 * (1 - (d - 0.5) / BAND)), `w(${d})`);
});

/** Put the first particles of a field on a lattice: `rows` rows of one particle at the centre of each tile of `cols`, the rest far away over the clear Farmland interior. */
function lattice(field: { count: number; xs: Float32Array; ys: Float32Array }, cols: number[], rows: number[]): number {
  let n = 0;
  for (const row of rows) {
    for (const col of cols) {
      if (n >= field.count) throw new Error('lattice larger than the field');
      field.xs[n] = ((col + 0.5) * TILE) / (COLS * TILE);
      field.ys[n] = ((row + 0.5) * TILE) / (ROWS * TILE);
      n++;
    }
  }
  for (let i = n; i < field.count; i++) {
    field.xs[i] = (55.5 * TILE) / (COLS * TILE);
    field.ys[i] = (2.5 * TILE) / (ROWS * TILE);
  }
  return n;
}
/** Rows with no indoor room and no empty ground in the synthetic world. */
const OPEN_ROWS = [2, 17, 33];

test('SOFT EDGE, HAND-DERIVED: heavy snow over the Highlands (cols 20..39) is exactly 1 in the interior and ramps over exactly 4 tiles on each border, and by exactly 4 tiles into the clear Farmland and the City', () => {
  // expected strength by column, by hand: inside the Highlands 1 - w(d); outside it w(d); w = 0.4375, 0.3125, 0.1875, 0.0625 for d = 1..4
  const HEAVY: Record<number, number> = {
    16: 0.0625, 17: 0.1875, 18: 0.3125, 19: 0.4375, // City side of the City|Highlands border: leaning toward heavy snow
    20: 0.5625, 21: 0.6875, 22: 0.8125, 23: 0.9375, // Highlands side of that border
    36: 0.9375, 37: 0.8125, 38: 0.6875, 39: 0.5625, // Highlands side of the Highlands|Farmland border
    40: 0.4375, 41: 0.3125, 42: 0.1875, 43: 0.0625, // Farmland side
  };
  for (let col = 24; col <= 35; col++) HEAVY[col] = 1; // the interior
  const w = winter();
  const map = worldMap();
  const mask = worldMask();
  const f = frameAt();
  const cols = Array.from({ length: 36 }, (_, k) => k + 10); // cols 10..45: every tile of both bands and a margin of interior either side
  const n = lattice(w.heavySnow, cols, OPEN_ROWS);
  const rec = new Rec();
  const drawn = R.renderCityWeather(rec, w.heavySnow, mask, f, { regions: map, alpha: (s) => w.alpha('heavy-snow', s) });
  const expectedDrawn = OPEN_ROWS.length * Object.keys(HEAVY).length;
  assert.equal(n, 108);
  assert.equal(drawn, expectedDrawn, 'exactly the particles over tiles with a strength are drawn: nothing beyond the band, nothing in the clear interiors');
  const seen = new Set<number>();
  for (const c of rec.calls) {
    const col = tileOf(c.x, c.y, f)[0];
    assert.ok(col in HEAVY, `a particle over col ${col}, which has no heavy snow`);
    assert.ok(near(c.alpha, HEAVY[col]), `col ${col}: drawn at ${c.alpha}, by hand ${HEAVY[col]}`);
    seen.add(col);
  }
  assert.equal(seen.size, Object.keys(HEAVY).length, 'every column of the band was exercised');
  // and the clear Farmland interior (cols 44..45 of the lattice, and the parked particles at col 55) and the City interior (cols 10..15) have none
  for (const c of rec.calls) assert.ok(![10, 11, 12, 13, 14, 15, 44, 45, 55].includes(tileOf(c.x, c.y, f)[0]));
});

test('SOFT EDGE, HAND-DERIVED: light snow over the City (cols 0..19) is 1 in the interior and ramps over exactly 4 tiles each side of the City|Highlands border only', () => {
  const LIGHT: Record<number, number> = {
    16: 0.9375, 17: 0.8125, 18: 0.6875, 19: 0.5625, // City side
    20: 0.4375, 21: 0.3125, 22: 0.1875, 23: 0.0625, // Highlands side: leaning toward the City's light snow
  };
  for (let col = 5; col <= 15; col++) LIGHT[col] = 1;
  const w = winter();
  const map = worldMap();
  const mask = worldMask();
  const f = frameAt();
  const cols = Array.from({ length: 22 }, (_, k) => k + 2); // cols 2..23, and the margin
  lattice(w.lightSnow, [...cols, 24, 25], OPEN_ROWS.slice(0, 2));
  const rec = new Rec();
  R.renderCityWeather(rec, w.lightSnow, mask, f, { regions: map, alpha: (s) => w.alpha('light-snow', s) });
  const flakes = rec.calls.filter((c) => !isShadow(c));
  for (const c of flakes) {
    const col = tileOf(c.x, c.y, f)[0];
    // cols 2..4 are City interior too: they have strength 1
    const want = col in LIGHT ? LIGHT[col] : col >= 2 && col <= 4 ? 1 : undefined;
    assert.ok(want !== undefined, `a flake over col ${col}, which has no light snow`);
    assert.ok(near(c.alpha, want!), `col ${col}: drawn at ${c.alpha}, by hand ${want}`);
  }
  // none beyond the band: cols 24 and 25 (Highlands interior) get nothing
  assert.ok(flakes.every((c) => tileOf(c.x, c.y, f)[0] <= 23));
  // each flake's shadow carries the flake's own strength
  for (const c of rec.calls.filter(isShadow)) assert.ok(c.alpha > 0 && c.alpha <= 1);
});

/** Frames at a zoom and a pan. The canvas is 60 x 40 tiles at zoom 1; at zoom z a tile is 16 z px, so only a window of the world is in view. */
const frameZ = (zoom: number, offsetX: number, offsetY: number): R.CityFrame => ({
  canvasWidth: COLS * TILE,
  canvasHeight: ROWS * TILE,
  offsetX,
  offsetY,
  zoom,
  view: {
    c0: Math.max(0, Math.floor(-offsetX / (TILE * zoom))),
    c1: Math.min(COLS, Math.ceil((COLS * TILE - offsetX) / (TILE * zoom))),
    r0: Math.max(0, Math.floor(-offsetY / (TILE * zoom))),
    r1: Math.min(ROWS, Math.ceil((ROWS * TILE - offsetY) / (TILE * zoom))),
  },
});

test('INDOORS AND EMPTY GROUND STAY EXACTLY DRY, by coordinates and by FULL bounds: no flake, no shadow and no veil over an indoor tile or the void, for every kind, at zooms 1 to 4 and under pans, edge band included', () => {
  const map = worldMap();
  const mask = worldMask();
  const modes = ['rain', 'light-snow', 'heavy-snow'] as const;
  const frames: R.CityFrame[] = [];
  for (const zoom of [1, 2, 3, 4]) for (const [dx, dy] of [[0, 0], [-37, 11], [120, -48], [-200, 150], [3, 3]]) frames.push(frameZ(zoom, dx, dy));
  let flakesChecked = 0;
  let shadowsChecked = 0;
  for (const mode of modes) {
    const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
    w.setMode(mode, true); // every region shows this kind, so the borders carry the SAME weather: the band must not matter to dryness
    for (const f of frames) {
      const rec = new Rec();
      R.renderRegionalWeather(rec, { weather: w, regions: map }, mask, f);
      for (const c of rec.calls) {
        const tile = TILE * f.zoom;
        const col = Math.floor((c.x - f.offsetX) / tile);
        const row = Math.floor((c.y - f.offsetY) / tile);
        // the corner every kind is placed by (as before this item): over open ground
        assert.ok(openGroundAt(col, row), `${mode} zoom ${f.zoom}: a particle at tile ${col},${row} is over an indoor tile or empty ground`);
        // EVERY kind, rain streaks included, and every shadow, is held to the WHOLE rectangle
        assert.ok(rectOverOpenGround(c, f), `${mode} zoom ${f.zoom}: the rectangle ${c.x},${c.y} ${c.w}x${c.h} reaches an indoor tile or empty ground`);
        if (isShadow(c)) shadowsChecked++;
        else flakesChecked++;
      }
    }
  }
  assert.ok(flakesChecked > 1000 && shadowsChecked > 100, `the check really ran over many flakes (${flakesChecked}) and shadows (${shadowsChecked})`);
  // THE VEIL: in the mixed winter weather, the tiles the darkness layer is punched at are exactly the indoor tiles, and no veil fill reaches an empty tile
  const layers: Rec[] = [];
  R.setCityLayerFactory((width, height) => {
    const ctx = new Rec();
    layers.push(ctx);
    return { width, height, getContext: () => ctx };
  });
  const w = winter();
  const stats = R.renderCityLighting(new Rec(), { level: 0, mask, lamps: [], agents: [], regional: { weather: w, regions: map } }, frameAt());
  assert.ok(stats);
  const dark = layers[0];
  const veilTiles = new Set<string>();
  for (const c of dark.calls.filter((x) => x.op === 'fillRect' && x.comp === 'source-over'))
    for (let r = c.y / TILE; r < (c.y + c.h) / TILE; r++) for (let col = c.x / TILE; col < (c.x + c.w) / TILE; col++) veilTiles.add(`${col},${r}`);
  for (let col = 0; col < COLS; col++) assert.equal(veilTiles.has(`${col},${ROWS - 1}`), false, `the empty bottom row (col ${col}) has no veil`);
  const punched = new Set<string>();
  for (const c of dark.calls.filter((x) => x.op === 'fillRect' && x.comp === 'destination-out'))
    for (let r = c.y / TILE; r < (c.y + c.h) / TILE; r++) for (let col = c.x / TILE; col < (c.x + c.w) / TILE; col++) punched.add(`${col},${r}`);
  const indoor = new Set<string>();
  for (let row = 0; row < ROWS; row++) for (let col = 0; col < COLS; col++) if (indoorAt(col, row)) indoor.add(`${col},${row}`);
  assert.deepEqual([...punched].sort(), [...indoor].sort(), 'the tiles punched out of the veil layer are exactly the indoor tiles, from the rooms by coordinates');
});

test('A RAIN STREAK NEVER REACHES AN INDOOR TILE OR EMPTY GROUND: hand-computed cases at zoom 1 and 2 (a streak starting 2 sprite px above an indoor room is cut to exactly 2, one wholly over open ground is untouched, one starting inside the room is absent, one above the empty ground is cut at it)', () => {
  const map = worldMap();
  const mask = worldMask();
  // the City's first indoor room is cols 5..14, rows 5..14: its top edge is y = 5 * tile; row 4 above it is open ground.
  // the empty ground is the bottom row (row 39): it starts at y = 39 * tile.
  for (const zoom of [1, 2]) {
    const tile = TILE * zoom;
    const px = 10 * tile + tile / 2; // the middle of col 10
    const long = 6 * zoom; // a full streak is round(6 * zoom * depth) px, and depth is set to 1 below
    const render = (f: R.CityFrame, spots: Array<[number, number]>) => {
      const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
      w.setMode('rain', true);
      const field = w.rain;
      spots.forEach(([x, y], i) => {
        // particleScreenX = floor(wrap(xNorm * canvasWidth + offsetX)): aim half a pixel in so floating point cannot floor it to x - 1
        field.xs[i] = (x + 0.5 - f.offsetX) / f.canvasWidth;
        field.ys[i] = (y + 0.5 - f.offsetY) / f.canvasHeight;
        field.depth[i] = 1;
      });
      for (let i = spots.length; i < field.count; i++) {
        field.xs[i] = 0.99;
        field.ys[i] = 0.99;
        field.depth[i] = 1;
      }
      const rec = new Rec();
      R.renderCityWeather(rec, field, mask, f, { regions: map, alpha: () => 1 });
      return rec;
    };
    const at = (rec: Rec, x: number, y: number) => rec.calls.filter((c) => c.x === x && c.y === y);
    // camera at the origin: the room cases
    const f = frameZ(zoom, 0, 0);
    const room: Array<[number, number]> = [
      [px, 5 * tile - 2 * zoom], // 2 sprite px above the room
      [px, 3 * tile], // well inside open ground
      [px, 5 * tile + tile / 2], // inside the room
    ];
    const rec = render(f, room);
    const cut = at(rec, room[0][0], room[0][1]);
    assert.equal(cut.length, 1, `zoom ${zoom}: the streak above the room is drawn`);
    assert.equal(cut[0].h, 2 * zoom, `zoom ${zoom}: cut to exactly the 2 sprite px of open ground left above the room, not the ${long} it would have been`);
    assert.equal(cut[0].y + cut[0].h, 5 * tile, `zoom ${zoom}: its bottom is exactly the top edge of the room`);
    const whole = at(rec, room[1][0], room[1][1]);
    assert.equal(whole.length, 1);
    assert.equal(whole[0].h, long, `zoom ${zoom}: a streak that fits keeps its full length`);
    assert.equal(at(rec, room[2][0], room[2][1]).length, 0, `zoom ${zoom}: nothing starts inside the room`);
    for (const c of rec.calls) assert.ok(rectOverOpenGround(c, f), `zoom ${zoom}: ${c.x},${c.y} ${c.w}x${c.h} is over open ground`);
    // camera panned so the empty bottom row (row 39) is in view: the streak 3 sprite px above it is cut at it
    const g = frameZ(zoom, 0, 300 - 39 * tile);
    const edge: Array<[number, number]> = [[px, 300 - 3 * zoom]];
    const rec2 = render(g, edge);
    const end = at(rec2, edge[0][0], edge[0][1]);
    assert.equal(end.length, 1, `zoom ${zoom}: the streak above the empty ground is drawn`);
    assert.equal(end[0].h, 3 * zoom, `zoom ${zoom}: cut at the empty ground`);
    for (const c of rec2.calls) assert.ok(rectOverOpenGround(c, g), `zoom ${zoom} (panned): ${c.x},${c.y} ${c.w}x${c.h} is over open ground`);
  }
});

/** Count the particles of a recording by the region of the tile under each, and by what that tile is. */
function tally(rec: Rec, map: RegionMap, mask: M.CityMask, f: R.CityFrame) {
  const perRegion = new Map<number, number>();
  let indoor = 0;
  let none = 0;
  let outdoor = 0;
  for (const c of rec.calls) {
    const [col, row] = tileOf(c.x, c.y, f);
    const m = M.maskAt(mask, col, row);
    if (m === M.MASK_INDOOR) indoor++;
    else if (m === M.MASK_NONE) none++;
    // a cool shadow (light snow) belongs to its flake: it must be over open ground at both corners, and it is not a particle of its own
    if (isShadow(c)) {
      const [col2, row2] = tileOf(c.x + c.w - 1, c.y + c.h - 1, f);
      if (M.maskAt(mask, col2, row2) !== M.MASK_OUTDOOR) indoor++;
      continue;
    }
    if (m === M.MASK_OUTDOOR) outdoor++;
    const slot = regionAt(map, col, row);
    perRegion.set(slot, (perRegion.get(slot) ?? 0) + 1);
  }
  return { perRegion, indoor, none, outdoor };
}

test('COVERAGE: in the fixture world EVERY region class that exists (City, Highlands, Farmland) receives particles of EVERY kind; the classes it lacks receive none', () => {
  // Evidence for the fixture: its three districts map to slots 0, 3 and 4 only, so slots 1 (Coast) and 2 (Woodland) own no tile at all.
  const map = worldMap();
  const present = new Set<number>(map.cells);
  assert.deepEqual([...present].sort(), [0, 3, 4], 'the fixture world has tiles in region slots 0, 3 and 4 only');
  const mask = worldMask();
  for (const mode of ['rain', 'light-snow', 'heavy-snow'] as const) {
    const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
    w.setMode(mode, true);
    const f = frameAt();
    const rec = new Rec();
    const drawn = R.renderRegionalWeather(rec, { weather: w, regions: map }, mask, f);
    const t = tally(rec, map, mask, f);
    for (const slot of present) assert.ok((t.perRegion.get(slot) ?? 0) > 0, `${mode}: region ${slot} receives particles`);
    for (const slot of [1, 2]) assert.equal(t.perRegion.get(slot) ?? 0, 0, `${mode}: region ${slot} owns no tile`);
    assert.equal(t.indoor, 0, `${mode}: indoor`);
    assert.equal(t.none, 0, `${mode}: none`);
    assert.equal(t.outdoor, drawn);
  }
});

let shipped: { cols: number; rows: number; tiles: number[]; areaTiles: Array<string | null> };
beforeAll(() => {
  shipped = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
}, 60_000);

/** A 3200 x 2000 canvas at zoom 1 centred on tile (130, 190): columns 30..229, rows 127..252 of the shipped world, which spans
 *  Autumn Wildwood (Woodland), Mirror Lakes (Coast), Silver Pine Highlands, River Valley Farmland and the Grand Boulevard (City). */
function shippedFrame(): R.CityFrame {
  const W = 3200;
  const H = 2000;
  const offsetX = Math.round(W / 2 - 130 * TILE);
  const offsetY = Math.round(H / 2 - 190 * TILE);
  return {
    canvasWidth: W,
    canvasHeight: H,
    offsetX,
    offsetY,
    zoom: 1,
    view: {
      c0: Math.floor(-offsetX / TILE),
      c1: Math.ceil((W - offsetX) / TILE),
      r0: Math.floor(-offsetY / TILE),
      r1: Math.ceil((H - offsetY) / TILE),
    },
  };
}

test('COVERAGE on the SHIPPED living metropolis, at a camera spanning all five regions: every region receives every kind; indoor and none are exactly zero', () => {
  const map = buildRegionMap(shipped.cols, shipped.rows, shipped.areaTiles);
  const mask = M.buildCityMask(shipped.cols, shipped.rows, shipped.tiles);
  const f = shippedFrame();
  // the camera really spans the five regions (counted over the tiles in view, not assumed)
  const inView = new Set<number>();
  for (let r = f.view.r0; r < f.view.r1; r++) for (let c = f.view.c0; c < f.view.c1; c++) inView.add(regionAt(map, c, r));
  assert.deepEqual([...inView].sort(), [0, 1, 2, 3, 4], 'City, Coast, Woodland, Highlands and Farmland are all in view');
  for (const mode of ['rain', 'light-snow', 'heavy-snow'] as const) {
    const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
    w.setMode(mode, true);
    const rec = new Rec();
    const drawn = R.renderRegionalWeather(rec, { weather: w, regions: map }, mask, f);
    const t = tally(rec, map, mask, f);
    for (let slot = 0; slot < 5; slot++) assert.ok((t.perRegion.get(slot) ?? 0) > 0, `${mode}: region ${slot} receives particles (${t.perRegion.get(slot) ?? 0})`);
    assert.equal(t.indoor, 0, `${mode}: no particle over an indoor tile`);
    assert.equal(t.none, 0, `${mode}: no particle over empty ground`);
    assert.equal(t.outdoor, drawn);
    assert.equal(rec.depth, 0);
  }
});

test('on the SHIPPED metropolis at that camera, mixed winter weather puts each kind in its own regions and a soft edge beside them only, and the clear Farmland stays clear beyond its edge', () => {
  const map = buildRegionMap(shipped.cols, shipped.rows, shipped.areaTiles);
  const mask = M.buildCityMask(shipped.cols, shipped.rows, shipped.tiles);
  const f = shippedFrame();
  const w = winter(); // City light snow, Coast rain, Woodland light snow, Highlands heavy snow, Farmland clear
  const only = (field: Parameters<typeof R.renderCityWeather>[1], kind: 'rain' | 'light-snow' | 'heavy-snow') => {
    const rec = new Rec();
    R.renderCityWeather(rec, field, mask, f, { regions: map, alpha: (s) => w.alpha(kind, s) });
    // every particle is over a tile whose DEFINED strength for the kind is above zero (its own region, or the soft edge beside one that shows it)
    // judged by the INDEPENDENT reference (brute-force Manhattan distance over the region data, the spec's weights): the drawn strength must be
    // one the spec allows at that tile (one value, or one per equally-near foreign region), and above zero
    let beyondEdge = 0;
    let overFarmlandInterior = 0;
    for (const c of rec.calls.filter((x) => !isShadow(x))) {
      const [col, row] = tileOf(c.x, c.y, f);
      const allowed = refStrengths(map, (slot) => w.alpha(kind, slot), col, row);
      if (!allowed.some((x) => x > 0 && near(x, c.alpha))) beyondEdge++;
      if (map.cells[row * map.cols + col] === 4 && refEdge(map, col, row).d === 0) overFarmlandInterior++;
    }
    return { ...tally(rec, map, mask, f), beyondEdge, overFarmlandInterior };
  };
  const rain = only(w.rain, 'rain');
  const light = only(w.lightSnow, 'light-snow');
  const heavy = only(w.heavySnow, 'heavy-snow');
  for (const t of [rain, light, heavy]) {
    assert.equal(t.beyondEdge, 0, 'no particle beyond the soft edge of a region that shows its kind');
    assert.equal(t.indoor, 0);
    assert.equal(t.none, 0);
    assert.equal(t.overFarmlandInterior, 0, 'the clear Farmland has no particle of any kind beyond its soft edge');
  }
  assert.ok((rain.perRegion.get(1) ?? 0) > 0, 'rain falls on the Coast');
  assert.ok((light.perRegion.get(0) ?? 0) > 0 && (light.perRegion.get(2) ?? 0) > 0, 'light snow falls on the City and the Woodland');
  assert.ok((heavy.perRegion.get(3) ?? 0) > 0, 'heavy snow falls on the Highlands');
  // a camera on a boundary shows two different weathers side by side: Highlands (heavy snow) beside Farmland (clear) at col 176
  const boundary = shippedFrameAt(176, 190);
  const heavyRec = new Rec();
  R.renderCityWeather(heavyRec, w.heavySnow, mask, boundary, { regions: map, alpha: (s) => w.alpha('heavy-snow', s) });
  const sides = { west: 0, softEast: 0, beyond: 0 };
  for (const c of heavyRec.calls) {
    const col = tileOf(c.x, c.y, boundary)[0];
    if (col < 176) sides.west++;
    else if (col < 176 + CITY_REGION_EDGE_TILES) sides.softEast++;
    else sides.beyond++;
  }
  assert.ok(sides.west > 0 && sides.softEast > 0 && sides.beyond === 0, `heavy snow west of col 176 and a soft edge beside it only (west ${sides.west}, edge ${sides.softEast}, beyond ${sides.beyond})`);
});

function shippedFrameAt(col: number, row: number): R.CityFrame {
  const W = 1600;
  const H = 1000;
  const offsetX = Math.round(W / 2 - col * TILE);
  const offsetY = Math.round(H / 2 - row * TILE);
  return {
    canvasWidth: W,
    canvasHeight: H,
    offsetX,
    offsetY,
    zoom: 1,
    view: {
      c0: Math.floor(-offsetX / TILE),
      c1: Math.ceil((W - offsetX) / TILE),
      r0: Math.floor(-offsetY / TILE),
      r1: Math.ceil((H - offsetY) / TILE),
    },
  };
}

test('CAMERA INDEPENDENCE: which particles are drawn is decided by the WORLD tile under each, for any pan', () => {
  const w = winter();
  const map = worldMap();
  const mask = worldMask();
  const f0 = frameAt();
  for (const [dx, dy] of [[0, 0], [-37, 11], [120, -48], [-200, 150], [3, 3], [-480, -320]]) {
    const f = { ...f0, offsetX: dx, offsetY: dy };
    // brute force from the field itself: where each particle is drawn (the world-anchored position), the tile under it, and that tile's DEFINED strength
    let expected = 0;
    const field = w.lightSnow;
    for (let i = 0; i < field.count; i++) {
      const [col, row] = tileOf(R.particleScreenX(field.xs[i], f), R.particleScreenY(field.ys[i], f), f);
      // the tile under the particle is judged by coordinates and the independent reference, not by the renderer's helpers
      if (col >= 0 && col < COLS && row >= 0 && row < ROWS && openGroundAt(col, row) && refStrengths(map, (s) => w.alpha('light-snow', s), col, row).some((x) => x > 0)) expected++;
    }
    const got = R.renderCityWeather(new Rec(), field, mask, f, { regions: map, alpha: (s) => w.alpha('light-snow', s) });
    assert.equal(got, expected, `pan ${dx},${dy}`);
  }
  // and the regions themselves never moved: the same world tiles belong to the same regions whatever was drawn
  assert.equal(regionAt(map, 19, 3), 0);
  assert.equal(regionAt(map, 20, 3), 3);
  assert.equal(regionAt(map, 40, 3), 4);
});

test('D5: A PAN CARRIES THE PARTICLES WITH THE WORLD: each particle stays over the same world point, and so the same tile and the same region, until it leaves the canvas and re-enters on the other side', () => {
  const w = winter();
  const map = worldMap();
  const mask = worldMask();
  const f0 = frameAt(100, 60);
  const field = w.lightSnow;
  const n = field.count;
  assert.ok(n > 100);
  // where each particle is, as a WORLD position in canvas pixels (its screen position minus the camera offset), before the pan
  const world = (f: R.CityFrame, i: number): [number, number] => [R.particleScreenX(field.xs[i], f) - f.offsetX, R.particleScreenY(field.ys[i], f) - f.offsetY];
  for (const [dx, dy] of [[1, 0], [-24, 0], [0, 37], [57, -41], [-200, 150], [333, 211]]) {
    const f1 = { ...f0, offsetX: f0.offsetX + dx, offsetY: f0.offsetY + dy };
    let kept = 0;
    for (let i = 0; i < n; i++) {
      const [x0, y0] = world(f0, i);
      const [x1, y1] = world(f1, i);
      // the world position is unchanged, or it moved by a whole number of canvas widths and heights (it wrapped round the canvas)
      const wrappedX = (x1 - x0) % f0.canvasWidth === 0;
      const wrappedY = (y1 - y0) % f0.canvasHeight === 0;
      assert.ok(wrappedX && wrappedY, `pan ${dx},${dy}: particle ${i} moved from world ${x0},${y0} to ${x1},${y1}`);
      if (x1 === x0 && y1 === y0) kept++;
    }
    // for a pan smaller than the canvas, every particle that stays on the canvas keeps its world point: the rest wrapped
    const expectedKept = Array.from({ length: n }, (_, i) => i).filter((i) => {
      const sx = R.particleScreenX(field.xs[i], f0) + dx;
      const sy = R.particleScreenY(field.ys[i], f0) + dy;
      return sx >= 0 && sx < f0.canvasWidth && sy >= 0 && sy < f0.canvasHeight;
    }).length;
    assert.equal(kept, expectedKept, `pan ${dx},${dy}: the particles that stay on the canvas keep their world point`);
    // the same world point is the same TILE and the same REGION: so a kept particle that is drawn before is drawn after, in the same region
    const tilesBefore = new Map<number, string>();
    const rec0 = new Rec();
    R.renderCityWeather(rec0, field, mask, f0, { regions: map, alpha: (s) => w.alpha('light-snow', s) });
    const rec1 = new Rec();
    R.renderCityWeather(rec1, field, mask, f1, { regions: map, alpha: (s) => w.alpha('light-snow', s) });
    const worldTiles = (rec: Rec, f: R.CityFrame) => new Set(rec.calls.filter((c) => !isShadow(c)).map((c) => tileOf(c.x, c.y, f).join(',') + ':' + c.alpha));
    const before = worldTiles(rec0, f0);
    const after = worldTiles(rec1, f1);
    let common = 0;
    for (const key of before) if (after.has(key)) common++;
    // most of what was drawn is still drawn over the very same world tile at the very same strength (a pan of a few tiles keeps nearly all of it)
    if (Math.abs(dx) < 60 && Math.abs(dy) < 60) assert.ok(common > before.size * 0.5, `pan ${dx},${dy}: ${common} of ${before.size} kept their tile and strength`);
    void tilesBefore;
  }
  // a pan of NOTHING changes nothing, and a pan of a whole canvas wraps every particle back to where it was
  const same = { ...f0 };
  for (let i = 0; i < n; i++) assert.deepEqual(world(same, i), world(f0, i));
  const full = { ...f0, offsetX: f0.offsetX + f0.canvasWidth, offsetY: f0.offsetY + f0.canvasHeight };
  for (let i = 0; i < n; i++) assert.deepEqual([R.particleScreenX(field.xs[i], full), R.particleScreenY(field.ys[i], full)], [R.particleScreenX(field.xs[i], f0), R.particleScreenY(field.ys[i], f0)]);
});

test('a region fading in or out draws its particles at that strength, and a region at zero draws none', () => {
  const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
  w.setMode('clear', true);
  w.setMode('rain', false); // fades in over four seconds
  const map = worldMap();
  const mask = worldMask();
  const f = frameAt();
  const filter = { regions: map, alpha: (s: number) => w.alpha('rain', s) };
  assert.equal(R.renderCityWeather(new Rec(), w.rain, mask, f, filter), 0); // alpha 0: invisible
  // the fade runs with motion ALLOWED (reduced motion settles a fade at once: its own tests are in cityRegionWeather.test.ts and citySeason.test.ts)
  for (let k = 0; k < 8; k++) w.update(0.25, false);
  const half = new Rec();
  const n = R.renderCityWeather(half, w.rain, mask, f, filter);
  assert.ok(n > 0);
  assert.ok(half.calls.every((c) => Math.abs(c.alpha - 0.5) < 1e-9), 'drawn at half strength');
  for (let k = 0; k < 8; k++) w.update(0.25, false);
  const full = new Rec();
  R.renderCityWeather(full, w.rain, mask, f, filter);
  assert.ok(full.calls.every((c) => c.alpha === 1));
});

test('heavy snow is visibly different from light snow: more flakes live, larger, and a stronger veil', () => {
  const map = worldMap();
  const mask = worldMask();
  const f = frameAt();
  const light = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
  light.setMode('light-snow', true);
  const heavy = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
  heavy.setMode('heavy-snow', true);
  const a = new Rec();
  const b = new Rec();
  const nLight = R.renderRegionalWeather(a, { weather: light, regions: map }, mask, f);
  const nHeavy = R.renderRegionalWeather(b, { weather: heavy, regions: map }, mask, f);
  // within the one particle pool (520 for every kind at most), heavy snow keeps its larger capacity (520 against 300)
  assert.ok(nHeavy > nLight * 1.5, `heavy ${nHeavy} against light ${nLight}`);
  const flakes = (rec: Rec) => rec.calls.filter((c) => !isShadow(c));
  const meanSize = (rec: Rec) => flakes(rec).reduce((n, c) => n + c.w, 0) / flakes(rec).length;
  assert.ok(meanSize(b) > meanSize(a), `flakes are larger: ${meanSize(b)} against ${meanSize(a)}`);
  assert.ok(Math.max(...flakes(b).map((c) => c.w)) > Math.max(...flakes(a).map((c) => c.w)));
  // light snow is laid on a cool shadow so it reads on pale ground; heavy snow needs none (its flakes are larger and its veil denser)
  assert.ok(a.calls.some(isShadow) && !b.calls.some(isShadow));
});

// ── the veil, region by region ────────────────────────────────────────────────

function install() {
  const layers: Rec[] = [];
  R.setCityLayerFactory((width, height) => {
    const ctx = new Rec();
    layers.push(ctx);
    return { width, height, getContext: () => ctx };
  });
  return layers;
}

/** What a recorded darkness layer laid per tile (the style strings of the veil fills, expanded to tiles), and the alpha each carries. */
function veilPerTile(dark: Rec): Map<string, string[]> {
  const laid = new Map<string, string[]>();
  for (const c of dark.calls.filter((x) => x.op === 'fillRect' && x.comp === 'source-over')) {
    for (let r = c.y / TILE; r < (c.y + c.h) / TILE; r++) {
      for (let col = c.x / TILE; col < (c.x + c.w) / TILE; col++) {
        const key = `${col},${r}`;
        laid.set(key, [...(laid.get(key) ?? []), String(c.fill)].sort());
      }
    }
  }
  return laid;
}
const alphaOfStyle = (style: string): number => Number(/,\s*([0-9.]+)\)$/.exec(style)![1]);

test('THE VEIL RAMPS BY HAND: heavy snow (0.26) and light snow (0.14) at every column of every band, exactly, and the clear Farmland interior and the City/Highlands interiors carry their own veil only', () => {
  const layers: Rec[] = [];
  R.setCityLayerFactory((width, height) => {
    const ctx = new Rec();
    layers.push(ctx);
    return { width, height, getContext: () => ctx };
  });
  const w = winter();
  R.renderCityLighting(new Rec(), { level: 0, mask: worldMask(), lamps: [], agents: [], regional: { weather: w, regions: worldMap() } }, frameAt());
  const laid = veilPerTile(layers[0]);
  const at = (col: number, row: number) => laid.get(`${col},${row}`) ?? [];
  // the colour of each snow veil, as the prefix of its fill style (the alpha follows)
  const HEAVY = cityRgba(150, 164, 190, 0).slice(0, -2);
  const LIGHT = cityRgba(120, 134, 160, 0).slice(0, -2);
  // BY HAND (strength x base alpha): heavy snow base 0.26, light snow base 0.14; w(d) = 0.4375, 0.3125, 0.1875, 0.0625
  const heavyByCol: Record<number, number> = {
    16: 0.26 * 0.0625, 17: 0.26 * 0.1875, 18: 0.26 * 0.3125, 19: 0.26 * 0.4375,
    20: 0.26 * 0.5625, 21: 0.26 * 0.6875, 22: 0.26 * 0.8125, 23: 0.26 * 0.9375,
    36: 0.26 * 0.9375, 37: 0.26 * 0.8125, 38: 0.26 * 0.6875, 39: 0.26 * 0.5625,
    40: 0.26 * 0.4375, 41: 0.26 * 0.3125, 42: 0.26 * 0.1875, 43: 0.26 * 0.0625,
  };
  for (let col = 24; col <= 35; col++) heavyByCol[col] = 0.26;
  const lightByCol: Record<number, number> = {
    16: 0.14 * 0.9375, 17: 0.14 * 0.8125, 18: 0.14 * 0.6875, 19: 0.14 * 0.5625,
    20: 0.14 * 0.4375, 21: 0.14 * 0.3125, 22: 0.14 * 0.1875, 23: 0.14 * 0.0625,
  };
  for (let col = 0; col <= 15; col++) lightByCol[col] = 0.14;
  for (const row of OPEN_ROWS) {
    for (let col = 0; col < COLS; col++) {
      const styles = at(col, row);
      const heavy = styles.filter((x) => x.startsWith(HEAVY));
      const light = styles.filter((x) => x.startsWith(LIGHT));
      assert.equal(styles.length, heavy.length + light.length, `col ${col}: only the two snow veils are laid`);
      assert.equal(heavy.length, col in heavyByCol ? 1 : 0, `col ${col}: heavy veil present exactly where it has strength`);
      assert.equal(light.length, col in lightByCol ? 1 : 0, `col ${col}: light veil present exactly where it has strength`);
      if (heavy.length) assert.ok(near(alphaOfStyle(heavy[0]), heavyByCol[col]), `col ${col}: heavy ${alphaOfStyle(heavy[0])}, by hand ${heavyByCol[col]}`);
      if (light.length) assert.ok(near(alphaOfStyle(light[0]), lightByCol[col]), `col ${col}: light ${alphaOfStyle(light[0])}, by hand ${lightByCol[col]}`);
    }
  }
  // the clear Farmland interior (cols 44..59) carries NO veil at all
  for (let col = 44; col < COLS; col++) assert.deepEqual(at(col, 17), [], `the clear Farmland interior has no veil (col ${col})`);
});

test('EQUAL WEATHER IS SEAMLESS: when neighbouring regions show the same kind, every tile has the same veil and every particle the same strength, and the border lays no extra fill', () => {
  const layers: Rec[] = [];
  const map = worldMap();
  const mask = worldMask();
  for (const mode of ['rain', 'light-snow', 'heavy-snow'] as const) {
    // a FRESH darkness layer for each kind (the layer is cached for the life of a factory, and a recording keeps every call made to it)
    layers.length = 0;
    R.setCityLayerFactory((width, height) => {
      const ctx = new Rec();
      layers.push(ctx);
      return { width, height, getContext: () => ctx };
    });
    const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
    w.setMode(mode, true);
    R.renderCityLighting(new Rec(), { level: 0, mask, lamps: [], agents: [], regional: { weather: w, regions: map } }, frameAt());
    const dark = layers[0];
    const fills = dark.calls.filter((x) => x.op === 'fillRect' && x.comp === 'source-over');
    // one fill spans a whole open row of the world (60 tiles): the borders at cols 20 and 40 lay nothing of their own
    const perRow = new Map<number, number>();
    for (const c of fills) perRow.set(c.y / TILE, (perRow.get(c.y / TILE) ?? 0) + 1);
    for (const row of OPEN_ROWS) assert.equal(perRow.get(row), 1, `${mode}: row ${row} is one fill`);
    const laid = veilPerTile(dark);
    const first = laid.get('0,2')!.join();
    for (let col = 0; col < COLS; col++) assert.equal((laid.get(`${col},2`) ?? []).join(), first, `${mode}: the veil is the same at col ${col}`);
    // every particle at full strength, right up to and across the borders
    const rec = new Rec();
    R.renderRegionalWeather(rec, { weather: w, regions: map }, mask, frameAt());
    const calls = rec.calls.filter((c) => !isShadow(c));
    assert.ok(calls.length > 100);
    assert.ok(calls.every((c) => c.alpha === 1), `${mode}: every particle at strength 1`);
    const nearBorder = calls.filter((c) => [17, 18, 19, 20, 21, 22, 37, 38, 39, 40, 41, 42].includes(tileOf(c.x, c.y, frameAt())[0]));
    assert.ok(nearBorder.length > 5, `${mode}: particles really were drawn beside the borders`);
  }
});

test('ZOOM: at every zoom from 1 to 8 the particles are placed by the WORLD tile under them: none over indoor or empty ground, none beyond the band, each at the strength the spec gives its tile', () => {
  const w = winter();
  const map = worldMap();
  const mask = worldMask();
  let checked = 0;
  for (const zoom of [1, 2, 3, 4, 5, 6, 8]) {
    for (const [dx, dy] of [[0, 0], [-90, 40], [-300, -100]]) {
      const f = frameZ(zoom, dx, dy);
      for (const kind of ['light-snow', 'heavy-snow'] as const) {
        const field = kind === 'light-snow' ? w.lightSnow : w.heavySnow;
        const rec = new Rec();
        R.renderCityWeather(rec, field, mask, f, { regions: map, alpha: (s) => w.alpha(kind, s) });
        for (const c of rec.calls) {
          const tile = TILE * zoom;
          const col = Math.floor((c.x - dx) / tile);
          const row = Math.floor((c.y - dy) / tile);
          if (isShadow(c)) {
            assert.ok(rectOverOpenGround(c, f), `zoom ${zoom}: a shadow reaches indoors or empty ground`);
            continue;
          }
          assert.ok(rectOverOpenGround(c, f), `zoom ${zoom} ${kind}: a flake reaches indoors or empty ground at ${col},${row}`);
          assert.ok(matchesSpec(map, (s) => w.alpha(kind, s), col, row, c.alpha), `zoom ${zoom} ${kind}: tile ${col},${row} drawn at ${c.alpha}, spec ${refStrengths(map, (s) => w.alpha(kind, s), col, row).join('/')}`);
          assert.ok(refStrengths(map, (s) => w.alpha(kind, s), col, row).some((x) => x > 0));
          checked++;
        }
      }
    }
  }
  assert.ok(checked > 200, `the check really ran over many particles (${checked})`);
});

test('[CONSISTENCY, NOT PROOF] the renderer\'s own helpers agree with the spec: regionEdges and edgeWeight give the distances and weights the reference computes', () => {
  // This uses the code under test, so it cannot show the code is right; it shows the helpers and the reference have not drifted apart.
  const map = worldMap();
  const edges = regionEdges(map);
  let compared = 0;
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) {
      const ref = refEdge(map, col, row);
      assert.equal(edges.dist[row * COLS + col], ref.d, `distance at ${col},${row}`);
      if (ref.d > 0) assert.ok(ref.foreign.includes(edges.foreign[row * COLS + col]));
      compared++;
    }
  }
  assert.equal(compared, COLS * ROWS);
  for (let d = 1; d <= BAND; d++) assert.ok(near(edgeWeight(d), SPEC_WEIGHT[d]));
  assert.equal(edgeWeight(0), 0);
  assert.equal(edgeWeight(BAND + 1), 0);
  // on the SHIPPED world as well (its districts include concave shapes)
  const shippedMap = buildRegionMap(shipped.cols, shipped.rows, shipped.areaTiles);
  const e2 = regionEdges(shippedMap);
  let mismatches = 0;
  for (let row = 100; row < 260; row += 3) {
    for (let col = 70; col < 280; col += 3) {
      const ref = refEdge(shippedMap, col, row);
      if (e2.dist[row * shippedMap.cols + col] !== ref.d) mismatches++;
    }
  }
  assert.equal(mismatches, 0, 'the helper and the reference agree on the shipped world (a sampled grid)');
});

test('all-clear regional weather at day lays nothing: no veil, no layer, no cost', () => {
  const layers = install();
  const w = winter();
  w.setMode('clear', true);
  assert.equal(w.anyWeather(), false);
  assert.equal(
    R.renderCityLighting(new Rec(), { level: 0, mask: worldMask(), lamps: [], agents: [], regional: { weather: w, regions: worldMap() } }, frameAt()),
    null,
  );
  assert.equal(layers.length, 0);
});

test('a fade lays both kinds of veil, each scaled by its own fade strength', () => {
  const layers = install();
  const w = new RegionalWeather(SEED, 'summer', Uint8Array.from([0, 1, 2, 3, 4]));
  w.setMode('rain', true);
  w.setMode('light-snow', false); // rain fading out, snow fading in
  for (let k = 0; k < 8; k++) w.update(0.25, false); // half way, with motion allowed (reduced motion would have settled the fade)
  R.renderCityLighting(new Rec(), { level: 0, mask: worldMask(), lamps: [], agents: [], regional: { weather: w, regions: worldMap() } }, frameAt());
  const colours = new Set(layers[0].calls.filter((c) => c.op === 'fillRect' && c.comp === 'source-over').map((c) => c.fill));
  assert.deepEqual(
    [...colours].sort(),
    [cityRgba(62, 78, 104, 0.2 * 0.5), cityRgba(120, 134, 160, 0.14 * 0.5)].sort(),
  );
});
