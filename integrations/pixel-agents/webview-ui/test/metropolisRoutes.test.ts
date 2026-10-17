/**
 * ROUTES OF THE SHIPPED METROPOLIS WORLD, judged by the RUNTIME (not a copy of its rules):
 * webview tileMap.isWalkable / findPath and layoutSerializer.getBlockedTiles, with the real bundled
 * furniture catalogue (footprints, background rows) loaded through buildDynamicCatalog.
 *
 * WATER is identified INDEPENDENTLY of the runtime's WATER_FLOOR_TILES: from the floor generators'
 * own pattern NAMES (scripts/generate-metropolis-floors.mjs, generate-forest-assets.mjs) — so the
 * water assertion can fail if the runtime ever lets a water floor be walked (shown below against a
 * deliberately broken predicate).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type {
  FurnitureAsset,
  FurnitureManifest,
  InheritedProps,
  ManifestGroup,
} from '../../core/src/assets/manifestUtils.ts';
import { flattenManifest } from '../../core/src/assets/manifestUtils.ts';
import { WATER_FLOOR_TILES } from '../src/constants.ts';
import { buildDynamicCatalog, getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { getBlockedTiles, layoutToTileMap } from '../src/office/layout/layoutSerializer.ts';
import { findPath, isWalkable } from '../src/office/layout/tileMap.ts';
import type { OfficeLayout, TileType as TileTypeVal } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLD: OfficeLayout = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'),
);
const BOARD = TileType.FLOOR_24; // boardwalk planks (generate-metropolis-floors.mjs)

function loadBundledCatalog(): void {
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const catalog: FurnitureAsset[] = [];
  for (const d of fs.readdirSync(dir)) {
    const m = JSON.parse(
      fs.readFileSync(path.join(dir, d, 'manifest.json'), 'utf-8'),
    ) as FurnitureManifest;
    if (m.type === 'asset') {
      catalog.push({
        id: m.id,
        name: m.name,
        label: m.name,
        category: m.category,
        file: m.file ?? `${m.id}.png`,
        width: m.width!,
        height: m.height!,
        footprintW: m.footprintW!,
        footprintH: m.footprintH!,
        isDesk: m.category === 'desks',
        canPlaceOnWalls: m.canPlaceOnWalls,
        canPlaceOnSurfaces: m.canPlaceOnSurfaces,
        backgroundTiles: m.backgroundTiles,
        groupId: m.id,
      });
      continue;
    }
    const inherited: InheritedProps = {
      groupId: m.id,
      name: m.name,
      category: m.category,
      canPlaceOnWalls: m.canPlaceOnWalls,
      canPlaceOnSurfaces: m.canPlaceOnSurfaces,
      backgroundTiles: m.backgroundTiles,
      ...(m.rotationScheme ? { rotationScheme: m.rotationScheme } : {}),
    };
    const root: ManifestGroup = {
      type: 'group',
      groupType: m.groupType as ManifestGroup['groupType'],
      rotationScheme: m.rotationScheme,
      members: m.members!,
    };
    catalog.push(...flattenManifest(root, inherited));
  }
  const sprites = Object.fromEntries(
    catalog.map((a) => [
      a.id,
      Array.from({ length: a.height }, () => new Array<string>(a.width).fill('')),
    ]),
  );
  assert.ok(buildDynamicCatalog({ catalog, sprites }));
}

/** Water tile values from the generators' pattern NAMES (independent of WATER_FLOOR_TILES). */
function namedWaterTiles(): number[] {
  const metro = fs.readFileSync(path.join(ROOT, 'scripts/generate-metropolis-floors.mjs'), 'utf-8');
  const names = [...metro.matchAll(/^\s*\[\s*'([^']+)',\s*pattern\(/gm)].map((m) => m[1]);
  assert.ok(names.length >= 10, 'metropolis floor pattern names found');
  const fromMetro = names.flatMap((n, i) => (/water/.test(n) ? [15 + i] : []));
  const forest = fs.readFileSync(path.join(ROOT, 'scripts/generate-forest-assets.mjs'), 'utf-8');
  const pond = /floor_13 -> FLOOR_(\d+): still water/.exec(forest);
  assert.ok(pond, 'the forest generator names its still-water pattern');
  return [Number(pond[1]), ...fromMetro].sort((a, b) => a - b);
}

let tileMap: TileTypeVal[][];
let blocked: Set<string>;
const W = WORLD.cols;
const H = WORLD.rows;
const label = (c: number, r: number) =>
  WORLD.areaTiles?.[r * W + c] ?? (c < 176 && r < 112 ? 'Palace Forest' : null);

beforeAll(() => {
  loadBundledCatalog();
  tileMap = layoutToTileMap(WORLD);
  blocked = getBlockedTiles(WORLD.furniture);
});

test('WATER: every water cell of the shipped world is NOT walkable by the runtime', () => {
  const water = namedWaterTiles();
  assert.deepEqual(water, [14, 22, 23], 'the named water floors');
  assert.deepEqual(
    [...WATER_FLOOR_TILES].sort((a, b) => a - b),
    water,
    'runtime excludes exactly them',
  );
  let waterCells = 0;
  let walkableWater = 0;
  let brokenWouldWalk = 0;
  const waterSet = new Set(water);
  for (let r = 0; r < H; r++)
    for (let c = 0; c < W; c++) {
      const t = tileMap[r][c];
      if (!waterSet.has(t)) continue;
      waterCells++;
      if (isWalkable(c, r, tileMap, blocked)) walkableWater++;
      // the PRE-FIX rule (only WALL/VOID excluded) — what this assertion exists to catch
      if (t !== TileType.WALL && t !== TileType.VOID && !blocked.has(`${c},${r}`))
        brokenWouldWalk++;
    }
  assert.ok(waterCells > 5_000, `the world has open water (${waterCells} cells)`);
  assert.equal(walkableWater, 0, 'no water cell is walkable at runtime');
  assert.ok(
    brokenWouldWalk > 5_000,
    'the assertion discriminates: the pre-fix rule would walk the sea',
  );
});

test('ROUTES: every district is reachable on foot from the palace interior (runtime findPath)', () => {
  const origin = { col: 88, row: 72 };
  assert.ok(isWalkable(origin.col, origin.row, tileMap, blocked), 'palace origin walkable');
  const targets = new Map<string, { col: number; row: number }>();
  for (let r = 0; r < H; r += 3)
    for (let c = 0; c < W; c += 3) {
      const l = label(c, r);
      if (
        l &&
        !targets.has(l) &&
        isWalkable(c, r, tileMap, blocked) &&
        (l !== 'Palace Forest' || r > 104 || r < 40)
      )
        targets.set(l, { col: c, row: r });
    }
  assert.equal(targets.size, 9, 'eight districts + the palace forest');
  for (const [l, t] of targets) {
    const route = findPath(origin.col, origin.row, t.col, t.row, tileMap, blocked);
    assert.ok(route.length > 0, `route palace -> ${l} (${t.col},${t.row})`);
  }
});

/** A copy of the tile map that keeps only columns [c0, c1) (VOID elsewhere): a LOCAL judgement. */
const windowed = (map: TileTypeVal[][], c0: number, c1: number, r0: number, r1: number) =>
  map.map((row, r) =>
    row.map((t, c) =>
      c >= c0 && c < c1 && r >= r0 && r < r1 ? t : (TileType.VOID as TileTypeVal),
    ),
  );

test('BRIDGES: each bridge carries a bank-to-bank crossing, and only it does there (deck removal)', () => {
  const water = new Set([14, 22, 23]);
  const nsBridges = [
    ['Valley Road bridge', 248, 251],
    ['harbour avenue bridge', 281, 283],
    ['farm footbridge', 205, 207],
  ] as const;
  for (const [name, c0, c1] of nsBridges) {
    // the river band beside the bridge, and the deck rows over it
    let top = -1;
    let bot = -1;
    for (let r = 120; r < 175; r++) {
      if (water.has(tileMap[r][c0 - 1])) {
        if (top < 0) top = r;
        bot = r;
      } else if (top >= 0) break;
    }
    assert.ok(top > 0, `${name}: river found`);
    const map = windowed(tileMap, c0 - 10, c1 + 10, top - 12, bot + 12);
    const from = { col: c0, row: top - 4 };
    const to = { col: c0, row: bot + 4 };
    const route = findPath(from.col, from.row, to.col, to.row, map, blocked);
    assert.ok(route.length > 0, `${name}: crossing exists`);
    assert.ok(
      route.some((p) => tileMap[p.row][p.col] === BOARD && water.has(tileMap[p.row][c0 - 1])),
      `${name}: the route uses the deck over the water`,
    );
    const noDeck = map.map((row, r) =>
      row.map((t, c) =>
        c >= c0 && c < c1 && r >= top - 2 && r <= bot + 2 && t === BOARD ? TileType.FLOOR_23 : t,
      ),
    );
    assert.equal(
      findPath(from.col, from.row, to.col, to.row, noDeck, blocked).length,
      0,
      `${name}: no crossing without its deck`,
    );
  }
  // the Highland stream stone bridge (east-west deck over the north-south stream): same proof
  const stone = WORLD.furniture.find((f) => f.type === 'DETAIL_BRIDGE_STONE')!;
  const row = stone.row + 2; // the deck row the trail runs on
  const deckCols: number[] = [];
  for (let c = stone.col - 4; c < stone.col + 10; c++)
    if (tileMap[row][c] === BOARD) deckCols.push(c);
  assert.ok(
    deckCols.some((c) => water.has(tileMap[row - 2][c]) && water.has(tileMap[row + 2][c])),
    'stone bridge: its deck spans the stream',
  );
  const sw = Math.min(...deckCols) - 3;
  const se = Math.max(...deckCols) + 3;
  const map = windowed(tileMap, sw - 2, se + 3, row - 8, row + 8);
  const route = findPath(sw, row, se, row, map, blocked);
  assert.ok(route.length > 0, 'stone bridge: crossing exists');
  assert.ok(
    route.some((p) => tileMap[p.row][p.col] === BOARD && water.has(tileMap[p.row - 2]?.[p.col])),
    'stone bridge: the route uses its deck over the stream',
  );
  const noDeck = map.map((r_, r) =>
    r_.map((t, c) =>
      deckCols.includes(c) && Math.abs(r - row) <= 1 && t === BOARD ? TileType.FLOOR_23 : t,
    ),
  );
  assert.equal(
    findPath(sw, row, se, row, noDeck, blocked).length,
    0,
    'stone bridge: no crossing without its deck',
  );
});

test('ARCHES AND GATES: drawn openings are walkable at runtime (all obstacles), legs and closed gates block', () => {
  const pieces = WORLD.furniture.filter((f) => /_P\d$/.test(f.type));
  assert.ok(pieces.length >= 4, 'split arches placed');
  for (const f of pieces) {
    const e = getCatalogEntry(f.type)!;
    const bottom = Array.from(
      { length: e.footprintW },
      (_, k) => [f.col + k, f.row + e.footprintH - 1] as const,
    );
    const opening = (e.backgroundTiles ?? 0) >= e.footprintH;
    for (const [c, r] of bottom)
      assert.equal(
        isWalkable(c, r, tileMap, blocked),
        opening,
        `${f.type} @${c},${r}: ${opening ? 'opening walkable' : 'leg blocks'}`,
      );
  }
  // the Grand Aquarium door: a runtime path from the lagoon promenade INTO the hall through the
  // tunnel arch's opening (a narrow window, so no other door can serve)
  const tunnel = WORLD.furniture.find((f) => f.type === 'AQUA_TUNNEL_ARCH_P2')!;
  const door = { col: tunnel.col, row: tunnel.row + getCatalogEntry(tunnel.type)!.footprintH - 1 };
  // the hall's ONLY entrance is its north door; reach a cell deep inside on the FULL map
  const wallRow = door.row + 1;
  assert.equal(
    tileMap[wallRow][door.col - 2],
    TileType.WALL,
    'the aquarium wall row is just south of the arch',
  );
  let inside: { col: number; row: number } | null = null;
  for (let r = wallRow + 14; r < wallRow + 26 && !inside; r++)
    for (let c = door.col - 6; c <= door.col + 6 && !inside; c++)
      if (isWalkable(c, r, tileMap, blocked)) inside = { col: c, row: r };
  assert.ok(inside, 'a walkable cell deep in the hall');
  // the start: the promenade cell nearest the arch on its north side (the lagoon is beyond it)
  let start: { col: number; row: number } | null = null;
  for (let r = door.row - 4; r < door.row && !start; r++)
    if (isWalkable(door.col, r, tileMap, blocked)) start = { col: door.col, row: r };
  assert.ok(start, 'a walkable promenade cell north of the arch');
  const route = findPath(start.col, start.row, inside.col, inside.row, tileMap, blocked);
  assert.ok(
    route.length > 0,
    'promenade -> through the tunnel arch -> deep into the Grand Aquarium',
  );
  assert.ok(
    route.some((p) => p.col === door.col && p.row === door.row),
    'the route passes the drawn opening',
  );
  // gates drawn CLOSED stay solid (and are never placed on a route)
  for (const g of WORLD.furniture.filter(
    (f) => f.type === 'FARM_GATE' || f.type === 'DETAIL_IRON_GATE',
  )) {
    const e = getCatalogEntry(g.type)!;
    const r = g.row + e.footprintH - 1;
    for (let k = 0; k < e.footprintW; k++)
      assert.equal(isWalkable(g.col + k, r, tileMap, blocked), false, `${g.type} closed`);
  }
});
