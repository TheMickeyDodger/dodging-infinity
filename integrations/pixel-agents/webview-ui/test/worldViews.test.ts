import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import {
  LEGACY_WORLD_MAX_DIM,
  MAX_COLS,
  MAX_ROWS,
  TILE_SIZE,
  WORLD_MAP_MARGIN_PX,
  ZOOM_MIN,
} from '../src/constants.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import {
  clampPanToWorld,
  confineTiles,
  connectedToAnchors,
  deckRegion,
  fitWorldMap,
  frameWorldView,
  isLargeWorld,
  livingRegions,
  livingTiles,
  mapPointToWorld,
  officeRegion,
  panToWorldPoint,
} from '../src/office/layout/worldViews.ts';
import { visibleTiles } from '../src/office/projection.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const readJson = (rel: string) => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf-8'));
const FOREST: OfficeLayout = readJson('packaging/di/worlds/vast-forest.json');
const EXPANDED: OfficeLayout = readJson('packaging/di/worlds/expanded-office.json');
const DEFAULT: OfficeLayout = readJson('webview-ui/public/assets/default-layout-1.json');

/** Footprints straight from the bundled manifests (groups flattened, `:left` = its side). */
function manifestFootprints(): FootprintOf {
  const map = new Map<string, { w: number; h: number; category: string }>();
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const visit = (node: Record<string, unknown>, category: string) => {
    const cat = (node.category as string | undefined) ?? category;
    if (node.type === 'asset')
      map.set(node.id as string, {
        w: node.footprintW as number,
        h: node.footprintH as number,
        category: cat,
      });
    for (const m of (node.members as Record<string, unknown>[] | undefined) ?? []) visit(m, cat);
  };
  for (const item of fs.readdirSync(dir))
    visit(JSON.parse(fs.readFileSync(path.join(dir, item, 'manifest.json'), 'utf-8')), '');
  CATEGORY = new Map([...map].map(([k, v]) => [k, v.category]));
  return (type) => map.get(type.replace(/:left$/, '')) ?? null;
}
let CATEGORY = new Map<string, string>();
const footprintOf = manifestFootprints();

// This Mac's office canvas (3024x1964 Retina, browser chrome, 420 px inspector): ~1092x860 CSS.
const BACKING_W = 2184;
const BACKING_H = 1720;
const DPR = 2;

test('legacy-size worlds keep their behaviour: no named views, no living-area confinement', () => {
  for (const layout of [DEFAULT, EXPANDED]) {
    assert.equal(isLargeWorld(layout.cols, layout.rows), false);
    assert.equal(frameWorldView('office', layout, footprintOf, BACKING_W, BACKING_H, DPR), null);
    assert.equal(livingRegions(layout, footprintOf), null);
  }
  assert.equal(isLargeWorld(LEGACY_WORLD_MAX_DIM, LEGACY_WORLD_MAX_DIM), false);
  assert.equal(isLargeWorld(LEGACY_WORLD_MAX_DIM + 1, 1), true);
});

test('the forest world fits the raised world ceiling', () => {
  assert.equal(FOREST.cols, 176);
  assert.equal(FOREST.rows, 112);
  assert.ok(FOREST.cols <= MAX_COLS && FOREST.rows <= MAX_ROWS);
  assert.equal(FOREST.tiles.length, FOREST.cols * FOREST.rows);
});

test('office region = the palace block; deck region = the DECK_* fittings north of it', () => {
  assert.deepEqual(officeRegion(FOREST), { c0: 56, r0: 40, c1: 120, r1: 104 });
  const deck = deckRegion(FOREST, footprintOf)!;
  assert.ok(deck);
  assert.ok(deck.r1 <= 40, 'the deck lies north of the palace');
  assert.ok(deck.c0 < 77 && deck.c1 > 84, 'the deck spans the lookout opening (cols 77-84)');
  assert.ok((deck.c1 - deck.c0) * (deck.r1 - deck.r0) >= 300, 'a substantial deck');
});

test('default OFFICE view: usable zoom (1 CSS px per sprite px on Retina), centred on the palace', () => {
  const f = frameWorldView('office', FOREST, footprintOf, BACKING_W, BACKING_H, DPR)!;
  assert.equal(f.zoom, 2); // whole-world fit would be 1: the speck the acceptance forbids
  const mapW = FOREST.cols * TILE_SIZE * f.zoom;
  const mapH = FOREST.rows * TILE_SIZE * f.zoom;
  // screen x of the palace centre = canvas centre (pan convention: offset = (canvas-map)/2 + pan)
  const cx = (BACKING_W - mapW) / 2 + f.panX + 88 * TILE_SIZE * f.zoom;
  const cy = (BACKING_H - mapH) / 2 + f.panY + 72 * TILE_SIZE * f.zoom;
  assert.ok(Math.abs(cx - BACKING_W / 2) <= 1);
  assert.ok(Math.abs(cy - BACKING_H / 2) <= 1);
});

test('DECK view shows the deck low in the frame with the forest beyond; WOODS is the min pixel-perfect zoom', () => {
  const d = frameWorldView('deck', FOREST, footprintOf, BACKING_W, BACKING_H, DPR)!;
  const deck = deckRegion(FOREST, footprintOf)!;
  const mapH = FOREST.rows * TILE_SIZE * d.zoom;
  const offY = (BACKING_H - mapH) / 2 + d.panY;
  const deckTop = offY + deck.r0 * TILE_SIZE * d.zoom;
  const deckBottom = offY + deck.r1 * TILE_SIZE * d.zoom;
  assert.ok(deckTop >= 0 && deckBottom <= BACKING_H, 'the whole deck is in view');
  assert.ok(deckTop > BACKING_H / 3, 'with forest above it');
  const o = frameWorldView('woods', FOREST, footprintOf, BACKING_W, BACKING_H, DPR)!;
  assert.equal(o.zoom, 1);
  assert.deepEqual([o.panX, o.panY], [0, 0]);
});

test('framing is re-derived for any canvas (resize) and the pan never leaves the world', () => {
  for (const [w, h, dpr] of [
    [2184, 1720, 2],
    [1200, 800, 1],
    [3800, 2000, 2],
    [640, 480, 1],
  ]) {
    for (const kind of ['office', 'deck', 'woods'] as const) {
      const f = frameWorldView(kind, FOREST, footprintOf, w, h, dpr)!;
      const c = clampPanToWorld(f.panX, f.panY, f.zoom, FOREST.cols, FOREST.rows, w, h);
      assert.deepEqual([c.x, c.y], [f.panX, f.panY], `${kind} ${w}x${h}`);
    }
  }
});

test('characters and pets LIVE in the office and on the deck only, in a large world', () => {
  const regions = livingRegions(FOREST, footprintOf)!;
  assert.equal(regions.length, 2);
  const tiles = [
    { col: 80, row: 60 }, // palace
    { col: 80, row: 30 }, // deck
    { col: 10, row: 10 }, // woodland
  ];
  assert.deepEqual(confineTiles(tiles, regions), tiles.slice(0, 2));
  assert.deepEqual(confineTiles(tiles, null), tiles);
  assert.deepEqual(confineTiles([{ col: 10, row: 10 }], regions), [{ col: 10, row: 10 }]); // never empty
});

test('viewport culling covers exactly the tiles in view (plus margin), clamped to the grid', () => {
  const v = visibleTiles(BACKING_W, BACKING_H, -2000, -1000, 2, 176, 112);
  assert.equal(v.c0, Math.floor(2000 / 32) - 1);
  assert.equal(v.r0, Math.floor(1000 / 32) - 1);
  assert.equal(v.c1, Math.ceil((BACKING_W + 2000) / 32) + 1);
  assert.equal(v.r1, Math.ceil((BACKING_H + 1000) / 32) + 1);
  const all = visibleTiles(10_000, 10_000, 0, 0, 1, 20, 11);
  assert.deepEqual(all, { c0: 0, c1: 20, r0: 0, r1: 11 });
});

test('the forest is scenery: no chair-category item outside the palace, none inside its block', () => {
  const inBlock = (c: number, r: number) => c >= 56 && c < 120 && r >= 40 && r < 104;
  const added = FOREST.furniture.filter((f) => /^(forest|deck)-/.test(f.uid));
  assert.ok(added.length > 0 && added.length <= 3_400);
  for (const f of added) {
    assert.ok(/^(FOREST|DECK)_/.test(f.type), f.type);
    assert.notEqual(CATEGORY.get(f.type), 'chairs', `${f.uid} would create a seat`);
    const fp = footprintOf(f.type)!;
    for (let dr = 0; dr < fp.h; dr++)
      for (let dc = 0; dc < fp.w; dc++)
        assert.ok(!inBlock(f.col + dc, f.row + dr), `${f.uid} overlaps the palace`);
    // the strip above the palace's north wall (its row -1 wall hangings) stays clear
    for (let dc = 0; dc < fp.w; dc++)
      assert.ok(
        !(f.row + fp.h - 1 >= 39 && f.row <= 39 && f.col + dc >= 56 && f.col + dc < 120),
        `${f.uid} sits in the wall-hanging strip`,
      );
  }
});

test('woodland is at least three times the palace land area (measured)', () => {
  let p = 0;
  let forest = 0;
  for (let r = 0; r < FOREST.rows; r++)
    for (let c = 0; c < FOREST.cols; c++) {
      if (FOREST.tiles[r * FOREST.cols + c] === TileType.VOID) continue;
      if (c >= 56 && c < 120 && r >= 40 && r < 104) p++;
      else forest++;
    }
  assert.equal(p, 3_939);
  assert.ok(forest >= 3 * p, `F=${forest} < 3P=${3 * p}`);
});

test('integer zoom CANNOT fit this world in the captured canvases — why the Map exists', () => {
  // backing 2184x1724 (1092x862 CSS @2x) and 1560x1520 (780x760 CSS @2x), and the same CSS at 1x
  for (const [w, h] of [
    [2184, 1724],
    [1560, 1520],
    [1092, 862],
    [780, 760],
  ]) {
    const zoom = frameWorldView('woods', FOREST, footprintOf, w, h, 1)!.zoom;
    assert.equal(zoom, ZOOM_MIN);
    assert.ok(FOREST.cols * TILE_SIZE * zoom > w, 'the world is wider than the canvas at zoom 1');
  }
});

test('the MAP fits the WHOLE world: all four edges inside the office area, DPR 1 and 2, both viewports', () => {
  const worldW = FOREST.cols * TILE_SIZE;
  const worldH = FOREST.rows * TILE_SIZE;
  // The map is laid out in CSS pixels; DPR only changes the backing store, never the fit.
  for (const dpr of [1, 2]) {
    for (const [cssW, cssH] of [
      [1092, 862],
      [780, 760],
    ]) {
      const r = fitWorldMap(worldW, worldH, cssW, cssH, WORLD_MAP_MARGIN_PX);
      const ctx = `${cssW}x${cssH} @${dpr}x`;
      assert.ok(r.scale < 1, `${ctx}: scaled down`);
      assert.ok(r.x >= 0 && r.y >= 0, `${ctx}: left/top edge inside`);
      assert.ok(r.x + r.w <= cssW && r.y + r.h <= cssH, `${ctx}: right/bottom edge inside`);
      assert.ok(Math.abs(r.w / r.h - worldW / worldH) < 0.02, `${ctx}: aspect kept`);
      // every world corner maps to a point inside the map rectangle
      for (const [wx, wy] of [
        [0, 0],
        [worldW, 0],
        [0, worldH],
        [worldW, worldH],
      ]) {
        const cx = r.x + wx * r.scale;
        const cy = r.y + wy * r.scale;
        assert.ok(cx >= 0 && cx <= cssW && cy >= 0 && cy <= cssH, `${ctx}: corner ${wx},${wy}`);
      }
      const centre = mapPointToWorld(r, r.x + r.w / 2, r.y + r.h / 2, worldW, worldH);
      assert.ok(
        Math.abs(centre.x - worldW / 2) < 2 / r.scale &&
          Math.abs(centre.y - worldH / 2) < 2 / r.scale,
      );
    }
  }
  // a small world is never scaled UP
  assert.equal(fitWorldMap(320, 176, 1092, 862, WORLD_MAP_MARGIN_PX).scale, 1);
});

test('Office and Deck framing are unchanged by the Map: integer zoom, as before', () => {
  for (const dpr of [1, 2]) {
    for (const kind of ['office', 'deck'] as const) {
      const f = frameWorldView(kind, FOREST, footprintOf, BACKING_W, BACKING_H, dpr)!;
      assert.ok(Number.isInteger(f.zoom) && f.zoom >= dpr, `${kind} @${dpr}x zoom ${f.zoom}`);
    }
  }
  const p = panToWorldPoint(
    88 * TILE_SIZE,
    72 * TILE_SIZE,
    2,
    FOREST.cols,
    FOREST.rows,
    BACKING_W,
    BACKING_H,
  );
  const o = frameWorldView('office', FOREST, footprintOf, BACKING_W, BACKING_H, DPR)!;
  assert.deepEqual([p.x, p.y], [o.panX, o.panY]);
});

test('living tiles: only what a seat can reach (a sealed pocket is excluded)', () => {
  // two walkable islands: seat beside island A only
  const tiles = [
    { col: 1, row: 1 },
    { col: 2, row: 1 },
    { col: 10, row: 10 },
    { col: 11, row: 10 },
  ];
  assert.deepEqual(connectedToAnchors(tiles, [{ col: 0, row: 1 }]), tiles.slice(0, 2));
  const regions = [{ c0: 0, r0: 0, c1: 20, r1: 20 }];
  assert.deepEqual(livingTiles(tiles, regions, [{ col: 0, row: 1 }]), tiles.slice(0, 2));
  assert.deepEqual(livingTiles(tiles, null, [{ col: 0, row: 1 }]), tiles); // legacy-size: all
});
