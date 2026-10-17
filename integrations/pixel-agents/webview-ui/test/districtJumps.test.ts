/**
 * MAP JUMPS land IN the district clicked, and a user zoom keeps them there (defect S-N1).
 *
 * For every district of the shipped metropolis world (its `areas` / `areaTiles`) — plus the
 * preserved palace forest, the null-labelled north-west quadrant, as the pseudo-district
 * "Palace Forest" — jump exactly as App.jumpToWorldPoint does (the Office view's usable zoom,
 * panToWorldPoint on the clicked point) to the district's own cell nearest its centroid, and read
 * the area label under the settled viewport centre (viewportCentreWorldPoint, the inverse of the
 * renderer's mapOffset). Then one zoom in and one zoom out with the button semantics (centre
 * anchor, panForZoomAroundPoint): still the same district. DPR 1 and DPR 2.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import { TILE_SIZE, ZOOM_MAX, ZOOM_MIN } from '../src/constants.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import {
  clampPanToWorld,
  frameWorldView,
  officeRegion,
  panForZoomAroundPoint,
  panToWorldPoint,
  viewportCentreWorldPoint,
} from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const METRO = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'),
) as OfficeLayout;
const { cols: COLS, rows: ROWS } = METRO;
const AREA_TILES = METRO.areaTiles ?? [];

/** The palace forest: the forest world preserved whole as the north-west quadrant. */
const PALACE_FOREST = 'Palace Forest';
const FOREST_QUADRANT = { cols: 176, rows: 112 };
/** The palace's own 64 x 64 block, as the forest world places it. */
const PALACE_HOME = { c0: 56, r0: 40, c1: 120, r1: 104 };

/** Footprints straight from the bundled manifests (groups flattened, `:left` = its side). */
function manifestFootprints(): FootprintOf {
  const map = new Map<string, { w: number; h: number; seat: boolean }>();
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const visit = (node: Record<string, unknown>, category: string) => {
    const cat = (node.category as string | undefined) ?? category;
    if (node.type === 'asset')
      map.set(node.id as string, {
        w: node.footprintW as number,
        h: node.footprintH as number,
        seat: cat === 'chairs',
      });
    for (const m of (node.members as Record<string, unknown>[] | undefined) ?? []) visit(m, cat);
  };
  for (const item of fs.readdirSync(dir)) {
    const file = path.join(dir, item, 'manifest.json');
    if (fs.existsSync(file)) visit(JSON.parse(fs.readFileSync(file, 'utf-8')), '');
  }
  return (type) => map.get(type.replace(/:left$/, '')) ?? null;
}
const footprintOf = manifestFootprints();

const CANVASES = [
  { w: 1152, h: 945, dpr: 1 },
  { w: 2304, h: 1890, dpr: 2 },
] as const;
type Canvas = (typeof CANVASES)[number];

/** The district a cell belongs to: its area label, or the palace forest for an unlabelled cell
 *  of the north-west quadrant; null for anything else. */
function districtAt(col: number, row: number): string | null {
  const label = AREA_TILES[row * COLS + col] ?? null;
  if (label !== null) return label;
  return col < FOREST_QUADRANT.cols && row < FOREST_QUADRANT.rows ? PALACE_FOREST : null;
}

const DISTRICTS = [...(METRO.areas ?? []).map((a) => a.label), PALACE_FOREST];

/** The district's own cell nearest the centroid of its cells (tile coordinates). */
function jumpCell(district: string): { col: number; row: number } {
  const cells: Array<{ col: number; row: number }> = [];
  for (let row = 0; row < ROWS; row++)
    for (let col = 0; col < COLS; col++)
      if (districtAt(col, row) === district) cells.push({ col, row });
  assert.ok(cells.length > 0, `${district} has cells`);
  const cx = cells.reduce((s, t) => s + t.col + 0.5, 0) / cells.length;
  const cy = cells.reduce((s, t) => s + t.row + 0.5, 0) / cells.length;
  let best = cells[0];
  let bestD = Infinity;
  for (const t of cells) {
    const d = (t.col + 0.5 - cx) ** 2 + (t.row + 0.5 - cy) ** 2;
    if (d < bestD) [best, bestD] = [t, d];
  }
  return best;
}
const JUMP_CELLS = new Map(DISTRICTS.map((d) => [d, jumpCell(d)]));

/** App.jumpToWorldPoint: the Office view's usable zoom, the camera centred on the point. */
function mapJump(c: Canvas, worldX: number, worldY: number) {
  const view = frameWorldView('office', METRO, footprintOf, c.w, c.h, c.dpr);
  assert.ok(view, 'a large world always has an Office view');
  const pan = panToWorldPoint(worldX, worldY, view.zoom, COLS, ROWS, c.w, c.h);
  return { zoom: view.zoom, pan };
}
function centreDistrict(c: Canvas, pan: { x: number; y: number }, zoom: number) {
  const p = viewportCentreWorldPoint(pan.x, pan.y, zoom, COLS, ROWS, c.w, c.h);
  return districtAt(Math.floor(p.x / TILE_SIZE), Math.floor(p.y / TILE_SIZE));
}

test('the districts: eight labelled areas and the palace forest, each a non-empty set of cells', () => {
  assert.deepEqual([COLS, ROWS], [352, 224]);
  assert.equal(AREA_TILES.length, COLS * ROWS);
  assert.equal(DISTRICTS.length, 9);
  assert.equal(new Set(DISTRICTS).size, DISTRICTS.length);
  for (const [d, cell] of JUMP_CELLS) assert.equal(districtAt(cell.col, cell.row), d);
});

for (const c of CANVASES) {
  for (const district of DISTRICTS) {
    test(`@${c.dpr}x Map jump to ${district}: the settled centre is in ${district}, through one zoom in and one out`, () => {
      const cell = JUMP_CELLS.get(district)!;
      const { zoom, pan } = mapJump(c, (cell.col + 0.5) * TILE_SIZE, (cell.row + 0.5) * TILE_SIZE);
      assert.equal(centreDistrict(c, pan, zoom), district, `jump to ${cell.col},${cell.row}`);
      assert.ok(zoom < ZOOM_MAX, 'there is a zoom step in');
      const zin = panForZoomAroundPoint(pan.x, pan.y, zoom, zoom + 1, COLS, ROWS, c.w, c.h);
      assert.equal(centreDistrict(c, zin, zoom + 1), district, 'after one zoom in');
      const zout = panForZoomAroundPoint(zin.x, zin.y, zoom + 1, zoom, COLS, ROWS, c.w, c.h);
      assert.equal(centreDistrict(c, zout, zoom), district, 'after one zoom out');
    });
  }

  test(`@${c.dpr}x zooming OUT below the jump zoom: a district is left ONLY where the world clamp must move the view`, () => {
    const { zoom } = mapJump(c, 0, 0);
    if (zoom <= ZOOM_MIN) return; // nothing below the jump zoom at this DPR
    const left: string[] = [];
    for (const district of DISTRICTS) {
      const cell = JUMP_CELLS.get(district)!;
      const { pan } = mapJump(c, (cell.col + 0.5) * TILE_SIZE, (cell.row + 0.5) * TILE_SIZE);
      const out = panForZoomAroundPoint(pan.x, pan.y, zoom, zoom - 1, COLS, ROWS, c.w, c.h);
      if (centreDistrict(c, out, zoom - 1) === district) continue;
      left.push(district);
      // left the district: the result is within the world, and keeping the centre would not be
      const loose = clampPanToWorld(out.x, out.y, zoom - 1, COLS, ROWS, c.w, c.h);
      assert.deepEqual(loose, out, `${district}: the result is within the world`);
      const before = viewportCentreWorldPoint(pan.x, pan.y, zoom, COLS, ROWS, c.w, c.h);
      const halfW = c.w / 2 / (zoom - 1);
      const halfH = c.h / 2 / (zoom - 1);
      const gap =
        before.x - halfW < 0 ||
        before.x + halfW > COLS * TILE_SIZE ||
        before.y - halfH < 0 ||
        before.y + halfH > ROWS * TILE_SIZE;
      assert.ok(gap, `${district}: left without the clamp being active`);
    }
    // the documented case: North Shore is 44 columns wide on the east coast, and a zoom-1 view at
    // DPR 2 is 144 tiles wide — its centre must sit 72 tiles in from the coast, in Metropolis
    if (c.dpr === 2 && zoom - 1 === ZOOM_MIN)
      assert.ok(left.includes('North Shore'), left.join(', '));
    assert.ok(left.length < DISTRICTS.length, 'most districts hold through the zoom out');
  });
}

test.skipIf(!METRO.home)(
  'the metropolis HOME is the palace block {c0:56,r0:40,c1:120,r1:104} (skipped only while metropolis.json lacks `home`)',
  () => {
    assert.deepEqual(METRO.home, PALACE_HOME);
    assert.deepEqual(officeRegion(METRO, footprintOf), PALACE_HOME);
  },
);
