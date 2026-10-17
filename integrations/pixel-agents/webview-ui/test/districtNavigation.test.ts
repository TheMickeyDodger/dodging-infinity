/**
 * DISTRICT NAVIGATION from layout.areas as DATA: every named district is listed, and framing it
 * lands the viewport centre (the renderer's own projection) on a cell of THAT district — at DPR 1
 * and 2, including the edge districts a widest-zoom framing could not centre. A layout with no
 * areas lists nothing; a layout with other areas lists those.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import { TILE_SIZE, ZOOM_MAX } from '../src/constants.ts';
import {
  districtsOf,
  frameDistrict,
  usableZoom,
  viewportCentreWorldPoint,
} from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLD: OfficeLayout = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'),
);
const SIZES = [
  { dpr: 1, w: 1152, h: 945 },
  { dpr: 2, w: 2304, h: 1890 },
];

const centreLabel = (
  layout: OfficeLayout,
  f: { zoom: number; panX: number; panY: number },
  w: number,
  h: number,
) => {
  const c = viewportCentreWorldPoint(f.panX, f.panY, f.zoom, layout.cols, layout.rows, w, h);
  return (
    layout.areaTiles?.[Math.floor(c.y / TILE_SIZE) * layout.cols + Math.floor(c.x / TILE_SIZE)] ??
    null
  );
};

test('every area with cells is listed, in the layout order, from the data', () => {
  const ds = districtsOf(WORLD);
  assert.deepEqual(
    ds.map((d) => d.label),
    (WORLD.areas ?? []).map((a) => a.label),
  );
  for (const d of ds)
    assert.equal(
      WORLD.areaTiles?.[d.target.row * WORLD.cols + d.target.col],
      d.label,
      `${d.label}: target is its own cell`,
    );
});

for (const { dpr, w, h } of SIZES)
  test(`DPR ${dpr}: each district's framing centres the viewport on that district`, () => {
    for (const d of districtsOf(WORLD)) {
      const f = frameDistrict(WORLD, d, w, h, dpr)!;
      assert.ok(f, `${d.label}: framed`);
      assert.equal(f.centred, true, `${d.label}: centred`);
      assert.equal(centreLabel(WORLD, f, w, h), d.label, `${d.label}: centre label`);
      assert.ok(
        f.zoom >= usableZoom(d.box, w, h, dpr) && f.zoom <= ZOOM_MAX,
        `${d.label}: zoom ${f.zoom}`,
      );
    }
  });

test('an edge district gets a closer zoom when the widest framing would clamp it off (North Shore, 3024 px at DPR 2)', () => {
  const d = districtsOf(WORLD).find((x) => x.label === 'North Shore')!;
  const [w, h] = [3024, 1890];
  const base = usableZoom(d.box, w, h, 2);
  const f = frameDistrict(WORLD, d, w, h, 2)!;
  assert.ok(
    f.zoom > base,
    `zoom ${f.zoom} > widest ${base}: at the widest zoom the east edge holds the camera in the Metropolis`,
  );
  assert.equal(f.centred, true);
  assert.equal(centreLabel(WORLD, f, w, h), 'North Shore');
});

test('no areas -> no districts; other areas -> those districts', () => {
  assert.deepEqual(districtsOf({ cols: 4, rows: 2 }), []);
  assert.deepEqual(
    districtsOf({ cols: 4, rows: 2, areas: [], areaTiles: new Array(8).fill(null) }),
    [],
  );
  /* eslint-disable pixel-agents/no-inline-colors -- area colours as layout test data */
  const small = {
    cols: 4,
    rows: 2,
    areas: [
      { label: 'East', color: '#000000' },
      { label: 'Unused', color: '#000000' },
      { label: 'West', color: '#000000' },
    ],
    areaTiles: ['West', 'West', 'East', 'East', 'West', 'West', 'East', 'East'],
  };
  /* eslint-enable pixel-agents/no-inline-colors */
  assert.deepEqual(
    districtsOf(small).map((d) => d.label),
    ['East', 'West'],
    'listed in the areas order; an area with no cells is not a place',
  );
});
