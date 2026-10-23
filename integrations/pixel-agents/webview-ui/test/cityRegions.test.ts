/**
 * ITEM 18, MILESTONE 1: the weather regions. A region map is a pure function of the layout's district labels, in WORLD
 * coordinates: it is derived on the SHIPPED living metropolis, on a world with no districts, and on a small layout.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import * as C from '../src/constants.ts';
import * as R from '../src/office/city/cityRegions.ts';

const WORLD = path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json');
let world: { cols: number; rows: number; tiles: number[]; areaTiles: Array<string | null> };
beforeAll(() => {
  world = JSON.parse(fs.readFileSync(WORLD, 'utf-8'));
}, 60_000);

const at = (map: R.RegionMap, col: number, row: number) => R.regionAt(map, col, row);

test('the bounds and the class numbers are exactly these values', () => {
  assert.equal(C.CITY_REGION_SLOTS, 5);
  assert.equal(C.CITY_REGION_FALLBACK_BANDS, 3);
  assert.equal(C.CITY_REGION_BAND_MIN_COLS, 96);
  assert.deepEqual(
    [R.REGION_CITY, R.REGION_COAST, R.REGION_WOODLAND, R.REGION_HIGHLANDS, R.REGION_FARMLAND],
    [0, 1, 2, 3, 4],
  );
  assert.equal(R.REGION_NONE, 255);
  assert.deepEqual(
    [...R.REGION_CLASS_NAMES],
    ['City', 'Coast', 'Woodland', 'Highlands', 'Farmland'],
  );
});

test('the label -> class table is exactly this, and covers every district label of the shipped world', () => {
  assert.deepEqual(R.REGION_LABEL_CLASS, {
    Metropolis: 0,
    'Grand Boulevard': 0,
    'Botanical Atrium': 0,
    'Deco Observatory': 0,
    'Cathedral Quarter': 0,
    Cathedral: 0,
    'Cathedral Parvis': 0,
    'Market Boulevard': 0,
    Target: 0,
    Walmart: 0,
    'Atrium Station': 0,
    'Observatory Station': 0,
    'Cathedral Station': 0,
    'Market Station': 0,
    'North Shore': 1,
    Harbour: 1,
    'Aquarium Quarter': 1,
    'Mirror Lakes': 1,
    'Autumn Wildwood': 2,
    'Silver Pine Highlands': 3,
    'River Valley Farmland': 4,
  });
  const labels = new Set(world.areaTiles.filter((l): l is string => l !== null));
  assert.equal(labels.size, 21);
  for (const label of labels)
    assert.ok(Object.hasOwn(R.REGION_LABEL_CLASS, label), `${label} is in the table`);
});

test('the shipped metropolis: each district lands in its class, in world coordinates', () => {
  const map = R.buildRegionMap(world.cols, world.rows, world.areaTiles);
  assert.equal(map.kind, 'districts');
  assert.equal(map.cells.length, 352 * 312);
  assert.equal(map.slots, 5);
  assert.deepEqual([...map.classes], [0, 1, 2, 3, 4]); // in a labelled world the index IS the class
  // a tile well inside each district (bounds from the world's own areaTiles)
  assert.equal(at(map, 250, 50), R.REGION_CITY); // Metropolis
  assert.equal(at(map, 330, 50), R.REGION_COAST); // North Shore
  assert.equal(at(map, 40, 150), R.REGION_WOODLAND); // Autumn Wildwood
  assert.equal(at(map, 130, 190), R.REGION_HIGHLANDS); // Silver Pine Highlands
  assert.equal(at(map, 220, 150), R.REGION_FARMLAND); // River Valley Farmland
  assert.equal(at(map, 120, 140), R.REGION_COAST); // Mirror Lakes
  assert.equal(at(map, 300, 140), R.REGION_COAST); // Harbour
  assert.equal(at(map, 300, 200), R.REGION_COAST); // Aquarium Quarter
  assert.equal(at(map, 200, 270), R.REGION_CITY); // Cathedral
  // the unlabelled north-west block (where the DI offices stand) takes the documented default: City
  assert.equal(at(map, 60, 60), R.REGION_CITY);
  assert.equal(at(map, 0, 0), R.REGION_CITY);
  // outside the map is nothing
  assert.equal(at(map, -1, 0), R.REGION_NONE);
  assert.equal(at(map, 352, 0), R.REGION_NONE);
  assert.equal(at(map, 0, 312), R.REGION_NONE);
  // every tile has a class, and all five classes exist in this world
  const seen = new Set<number>();
  for (const v of map.cells) seen.add(v);
  assert.deepEqual([...seen].sort(), [0, 1, 2, 3, 4]);
  // two regions meet at a hard, world-fixed boundary: Highlands (cols 88..175) and Farmland (cols 176..263) on row 190
  assert.equal(at(map, 175, 190), R.REGION_HIGHLANDS);
  assert.equal(at(map, 176, 190), R.REGION_FARMLAND);
});

test('the map is derived ONCE per layout and cached, so the camera, zoom and jumps can never change it', () => {
  const layout = {
    cols: world.cols,
    rows: world.rows,
    tiles: world.tiles,
    areaTiles: world.areaTiles,
  };
  const a = R.regionMapFor(layout);
  const b = R.regionMapFor({ ...layout });
  // identity only (never assert.equal on a map: a failure would print its 109,824 cells)
  assert.ok(a === b, 'the same object: nothing about the view enters its key');
  const snapshot = [at(a, 130, 190), at(a, 176, 190), at(a, 60, 60)];
  for (let k = 0; k < 100; k++) R.regionMapFor(layout); // any amount of "panning" re-reads the same map
  assert.deepEqual([at(a, 130, 190), at(a, 176, 190), at(a, 60, 60)], snapshot);
  // a different layout gets its own map
  assert.ok(R.regionMapFor({ cols: 10, rows: 10, tiles: new Array(100).fill(1) }) !== a);
});

test('a world WITHOUT districts and at least 96 columns wide is split into three equal bands, west to east', () => {
  const cols = 352;
  const rows = 40;
  const map = R.buildRegionMap(cols, rows, undefined);
  assert.equal(map.kind, 'bands');
  assert.equal(map.slots, 3);
  assert.deepEqual([...map.classes], [0, 0, 0, 0, 0]); // fallback regions use the City row
  assert.equal(at(map, 0, 0), 0);
  assert.equal(at(map, 117, 5), 0);
  assert.equal(at(map, 118, 5), 1); // floor(118 * 3 / 352) = 1
  assert.equal(at(map, 234, 5), 1);
  assert.equal(at(map, 235, 5), 2);
  assert.equal(at(map, 351, 39), 2);
  assert.deepEqual(
    [R.regionName(map, 0), R.regionName(map, 1), R.regionName(map, 2)],
    ['West', 'Middle', 'East'],
  );
  // exactly 96 columns is wide enough; 95 is not
  assert.equal(R.buildRegionMap(96, 4, undefined).kind, 'bands');
  assert.equal(R.buildRegionMap(95, 4, undefined).kind, 'single');
});

test('a small layout is ONE region everywhere, and always covered', () => {
  const small = R.buildRegionMap(20, 11, undefined);
  assert.equal(small.kind, 'single');
  assert.equal(small.slots, 1);
  for (let r = 0; r < 11; r++) for (let c = 0; c < 20; c++) assert.equal(at(small, c, r), 0);
  assert.equal(R.regionName(small, 0), 'Everywhere');
  // an empty area array, or one that carries labels the table does not know, is "no districts"
  assert.equal(R.buildRegionMap(20, 11, new Array(220).fill(null)).kind, 'single');
  assert.equal(R.buildRegionMap(20, 11, new Array(220).fill('Imported Wing')).kind, 'single');
});

test('THE SOFT EDGE of an imported layout: a banded world ramps over exactly 4 tiles at each of its two band borders (hand-derived columns), and a single-region world has no edge at all', () => {
  // 352 columns, no districts: three bands, borders between columns 117|118 and 234|235 (floor(col * 3 / 352), as asserted above).
  const map = R.buildRegionMap(352, 4, undefined);
  const { dist, foreign } = R.regionEdges(map);
  const col = (c: number, r = 2) => r * 352 + c;
  // WEST|MIDDLE: the west tiles 114..117 are 4,3,2,1 tiles from the middle (foreign slot 1); the middle tiles 118..121 are 1,2,3,4 from the west (foreign slot 0)
  assert.deepEqual(
    [113, 114, 115, 116, 117].map((c) => dist[col(c)]),
    [0, 4, 3, 2, 1],
  );
  assert.deepEqual(
    [118, 119, 120, 121, 122].map((c) => dist[col(c)]),
    [1, 2, 3, 4, 0],
  );
  assert.deepEqual(
    [114, 115, 116, 117].map((c) => foreign[col(c)]),
    [1, 1, 1, 1],
  );
  assert.deepEqual(
    [118, 119, 120, 121].map((c) => foreign[col(c)]),
    [0, 0, 0, 0],
  );
  // MIDDLE|EAST: middle 231..234 are 4,3,2,1 from the east (foreign 2); east 235..238 are 1,2,3,4 from the middle (foreign 1)
  assert.deepEqual(
    [230, 231, 232, 233, 234].map((c) => dist[col(c)]),
    [0, 4, 3, 2, 1],
  );
  assert.deepEqual(
    [235, 236, 237, 238, 239].map((c) => dist[col(c)]),
    [1, 2, 3, 4, 0],
  );
  assert.deepEqual(
    [231, 232, 233, 234].map((c) => foreign[col(c)]),
    [2, 2, 2, 2],
  );
  assert.deepEqual(
    [235, 236, 237, 238].map((c) => foreign[col(c)]),
    [1, 1, 1, 1],
  );
  // the same on every row (a vertical border), and nowhere else: 8 edge columns per border, 16 in all
  for (let r = 0; r < 4; r++) {
    let edgeColumns = 0;
    for (let c = 0; c < 352; c++) if (dist[col(c, r)] > 0) edgeColumns++;
    assert.equal(edgeColumns, 16, `row ${r}`);
    assert.equal(
      dist[col(0, r)] + dist[col(176, r)] + dist[col(351, r)],
      0,
      'band interiors and the map edge are never edge',
    );
  }
  // A SINGLE region (a small layout, or an imported one with no known districts) has nothing to blend toward
  for (const single of [
    R.buildRegionMap(20, 11, undefined),
    R.buildRegionMap(95, 4, undefined),
    R.buildRegionMap(20, 11, new Array(220).fill('Imported Wing')),
  ]) {
    assert.equal(single.kind, 'single');
    assert.equal(
      R.regionEdges(single).dist.every((d) => d === 0),
      true,
      'no edge anywhere in a single region',
    );
  }
  // and it is cached per map: the same object for the same map, a different one for a different map
  assert.ok(R.regionEdges(map) === R.regionEdges(map));
});

test('in a labelled world an unknown label, or none, takes the City class', () => {
  const areas: Array<string | null> = new Array(40).fill(null);
  areas[0] = 'Silver Pine Highlands';
  areas[1] = 'Some Imported Wing'; // unknown to the table
  areas[2] = '__proto__'; // never read through the object's prototype
  areas[3] = 'toString';
  const map = R.buildRegionMap(10, 4, areas);
  assert.equal(map.kind, 'districts');
  assert.deepEqual([...map.cells.slice(0, 5)], [3, 0, 0, 0, 0]);
});

test('region names are plain words', () => {
  const map = R.buildRegionMap(world.cols, world.rows, world.areaTiles);
  assert.deepEqual(
    [0, 1, 2, 3, 4].map((slot) => R.regionName(map, slot)),
    ['City', 'Coast', 'Woodland', 'Highlands', 'Farmland'],
  );
});
