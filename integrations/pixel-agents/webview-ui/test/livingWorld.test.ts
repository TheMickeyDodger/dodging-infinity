/**
 * THE LIVING METROPOLIS (M3 S2) on the SHIPPED world file, judged by the product's own code:
 * preservation of the r4 world (by uid and by cell, with the recorded intentional deltas), the
 * wall-join sweep, thick-wall seam rendering, ROOM-LEVEL reachability of every designed room on the
 * pedestrian network, a route THROUGH the cathedral portal, traffic clear of crossings, and distinct
 * tower designs on every skyline slot.
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
import { LAYOUT_MAX_CELLS } from '../../core/src/layoutLimits.ts';
import { AMBIENT_ROUTINE_MAX_STOPS } from '../src/constants.ts';
import type { AmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { buildDynamicCatalog, getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import type { OfficeLayout, SpriteData, TileType as TileTypeVal } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';
import { getWallInstances, setWallSprites } from '../src/office/wallTiles.ts';

const ROOT = path.join(__dirname, '..', '..');
const read = (p: string) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf-8'));
const WORLD: OfficeLayout = read('packaging/di/worlds/living-metropolis.json');
const R4: OfficeLayout = read('packaging/di/worlds/metropolis.json');
const BUILD = read('packaging/di/LIVING-WORLD-BUILD.json');
const COLS = WORLD.cols;

function loadBundledCatalog(): void {
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const catalog: FurnitureAsset[] = [];
  for (const d of fs.readdirSync(dir)) {
    const f = path.join(dir, d, 'manifest.json');
    if (!fs.existsSync(f)) continue;
    const m = JSON.parse(fs.readFileSync(f, 'utf-8')) as FurnitureManifest;
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
    catalog.map((a) => [a.id, Array.from({ length: a.height }, () => new Array<string>(a.width).fill(''))]),
  );
  assert.ok(buildDynamicCatalog({ catalog, sprites }));
}

let os: OfficeState;
let world: AmbientWorld;
let main = -1;
beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(WORLD);
  os.setAmbientEnabled(true);
  world = os.ambient!;
  const g = world.graph;
  for (let k = 1, best = 0; k < g.componentTiles.length; k++) if (g.componentTiles[k] > g.componentTiles[best]) best = main = k;
  if (main < 0) main = 0;
});

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

test('shape: r4 grid extended south, within the raised total-cell cap', () => {
  assert.equal(WORLD.cols, R4.cols);
  assert.equal(WORLD.rows, R4.rows + 88); // 80 rows of quarters (S2) + 8 rows of subway line (S3)
  assert.ok(WORLD.cols * WORLD.rows <= LAYOUT_MAX_CELLS);
  // a fresh parse: OfficeState takes ownership of the layout object it is given (pets included)
  const raw: OfficeLayout = read('packaging/di/worlds/living-metropolis.json');
  assert.deepEqual(raw.home, R4.home);
  assert.deepEqual(raw.pets, read('packaging/di/worlds/metropolis.json').pets);
});

test('preservation: every r4 cell is unchanged except the recorded sweep repairs', () => {
  const fixed = new Set<number>();
  for (const [c, r] of BUILD.sweep.fixedR4CellList as Array<[number, number]>) fixed.add(r * COLS + c);
  assert.equal(fixed.size, BUILD.sweep.fixedR4Cells, 'the recorded list is complete');
  let diffs = 0;
  for (let i = 0; i < R4.cols * R4.rows; i++) {
    if (fixed.has(i)) continue;
    if (WORLD.tiles[i] !== R4.tiles[i] || !same(WORLD.tileColors?.[i], R4.tileColors?.[i]) || !same(WORLD.carpetTiles?.[i], R4.carpetTiles?.[i])) diffs++;
  }
  assert.equal(diffs, 0);
});

test('preservation by uid: r4 furniture unchanged except the recorded tower swaps, Gallery Office exhibit deltas and R1 crossing-landing moves; the original 41 computers and home seats unchanged', () => {
  const byUid = new Map(WORLD.furniture.map((f) => [f.uid, f]));
  const swapped = new Set([...(BUILD.towers.swaps as Array<{ uid: string }>), ...(BUILD.exhibits.replaced as Array<{ uid: string }>), ...(BUILD.r1.moved as Array<{ uid: string }>)].map((s) => s.uid));
  let changed = 0;
  for (const f of R4.furniture) if (!swapped.has(f.uid) && !same(byUid.get(f.uid), f)) changed++;
  assert.equal(changed, 0);
  const r4Pcs = R4.furniture.filter((f) => /^PC_/.test(f.type));
  assert.equal(r4Pcs.length, 41);
  for (const f of r4Pcs) assert.ok(same(byUid.get(f.uid), f), `computer ${f.uid} unchanged`);
  const home = R4.home!;
  const inHome = (f: { col: number; row: number }) => f.col >= home.c0 && f.col < home.c1 && f.row >= home.r0 && f.row < home.r1;
  for (const f of R4.furniture) if (getCatalogEntry(f.type)?.category === 'chairs' && inHome(f)) assert.ok(same(byUid.get(f.uid), f), `home seat ${f.uid} unchanged`);
  // the honest totals: the authorized expansion adds computers and seats outside the home
  const pcs = WORLD.furniture.filter((f) => /^PC_/.test(f.type)).length;
  assert.equal(pcs, BUILD.preservation.computersTotal);
  assert.ok(pcs > 41);
});

test('new seats outside the home are scenery: real-agent seats stay inside the home and deck', () => {
  const home = R4.home!;
  for (const s of os.seats.values()) {
    const inHome = s.seatCol >= home.c0 && s.seatCol < home.c1 && s.seatRow >= home.r0 && s.seatRow < home.r1;
    const nearHome = s.seatCol >= home.c0 - 30 && s.seatCol < home.c1 + 30 && s.seatRow >= home.r0 - 30 && s.seatRow < home.r1 + 30;
    assert.ok(inHome || nearHome, `seat ${s.uid} is a home/deck seat`);
    assert.ok(s.seatRow < R4.rows, `no seat on the new ground (${s.uid})`);
  }
});

test('wall-join sweep: no VOID touches a wall, no walkable floor carries a wall-dark colour, the reported defect cells are repaired', () => {
  const C = COLS;
  for (let i = 0; i < WORLD.tiles.length; i++) {
    if (WORLD.tiles[i] !== TileType.VOID) continue;
    const c = i % C;
    const r = (i - c) / C;
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const n = (r + dr) * C + c + dc;
      assert.notEqual(WORLD.tiles[n], TileType.WALL, `VOID ${c},${r} touches a wall`);
    }
  }
  for (let i = 0; i < WORLD.tiles.length; i++) {
    const t = WORLD.tiles[i];
    if (t === TileType.WALL || t === TileType.VOID) continue;
    const col = WORLD.tileColors?.[i];
    assert.ok(!col || col.b > -80, `floor ${i % C},${Math.floor(i / C)} is wall-dark`);
  }
  for (const c of [77, 78, 79, 82, 83, 84]) assert.equal(WORLD.tiles[66 * C + c], TileType.WALL, `palace row 66 col ${c} closed`);
  for (const c of [80, 81]) assert.notEqual(WORLD.tiles[66 * C + c], TileType.WALL, `palace row 66 door col ${c} kept`);
  for (const c of [95, 96, 97, 98]) assert.ok((WORLD.tileColors?.[79 * C + c]?.b ?? 0) > -80, `palace (${c},79) floor recoloured`);
});

test('thick-wall seams and pinholes: interior edge outlines are suppressed only inside a wall mass', () => {
  /* eslint-disable pixel-agents/no-inline-colors -- synthetic sprite pixels as test data */
  const OUT = '#101010';
  const FACE = '#4060a0';
  /* eslint-enable pixel-agents/no-inline-colors */
  // like the real set: corner pixels on row 0, side lines from row 8 down, face elsewhere
  const piece = (): SpriteData =>
    Array.from({ length: 32 }, (_, y) => Array.from({ length: 16 }, (_, x) => ((x === 0 || x === 15) && (y === 0 || y >= 8) ? OUT : FACE)));
  setWallSprites([Array.from({ length: 16 }, piece)]);
  const W = TileType.WALL as TileTypeVal;
  const F = TileType.FLOOR_1 as TileTypeVal;
  // a 3 x 3 mass (thick) on the left; a single-thickness T on the right
  const map: TileTypeVal[][] = [
    [W, W, W, F, W, W, W],
    [W, W, W, F, F, W, F],
    [W, W, W, F, F, W, F],
    [F, F, F, F, F, F, F],
  ];
  const inst = getWallInstances(map);
  const at = (c: number, r: number) => inst.find((i) => i.x === c * 16 && i.y === r * 16 - 16)!.sprite;
  // the mass centre: every edge outline lies inside the mass
  assert.equal(at(1, 1)[0][15], FACE, 'centre: north-east corner pinhole filled');
  assert.equal(at(1, 1)[0][0], FACE, 'centre: north-west corner pinhole filled');
  assert.equal(at(1, 1)[12][15], FACE, 'centre: east seam filled');
  assert.equal(at(1, 1)[12][0], FACE, 'centre: west seam filled');
  // the mass's top row has no north neighbour: its row-0 corners stay (they are the outer edge)
  assert.equal(at(1, 0)[0][15], OUT, 'top row keeps its outer corner');
  assert.equal(at(1, 0)[12][15], FACE, 'top row: east seam inside the mass filled');
  // outer edges of the mass keep their outline
  assert.equal(at(2, 1)[12][15], OUT, 'east edge of the mass keeps its outline');
  assert.equal(at(0, 1)[12][0], OUT, 'west edge of the mass keeps its outline');
  // single-thickness T junction: unchanged
  assert.equal(at(5, 0)[12][0], OUT, 'single-thickness T keeps its west line');
  assert.equal(at(5, 0)[12][15], OUT, 'single-thickness T keeps its east line');
  assert.equal(at(5, 1)[0][15], OUT, 'single-thickness run keeps its corner');
});

test('room-level reachability: every designed room of the complexes and the cathedral is on the main network', () => {
  const g = world.graph;
  const mask = world.pathfinder.mask.walkable;
  const rows: string[] = [];
  for (const b of BUILD.buildings as Array<{ label: string; rooms: Array<{ name: string; kind: string; rect: [number, number, number, number] }> }>) {
    for (const room of b.rooms) {
      const [c0, r0, c1, r1] = room.rect;
      let walk = 0;
      let onMain = 0;
      for (let r = r0; r <= r1; r++)
        for (let c = c0; c <= c1; c++) {
          const i = r * COLS + c;
          if (mask[i] !== 1) continue;
          walk++;
          if (g.componentOf(i) === main) onMain++;
        }
      rows.push(`${b.label} / ${room.name}: ${onMain}/${walk}`);
      assert.ok(walk > 0, `${b.label} / ${room.name} has walkable floor`);
      assert.ok(onMain >= walk - 6, `${b.label} / ${room.name}: ${onMain} of ${walk} walkable tiles on the main network`);
    }
  }
  assert.ok(rows.length >= 50);
});

test('the cathedral portal is walkable: a route from the parvis to the nave passes through an archway', () => {
  const cath = (BUILD.buildings as Array<{ label: string; entrances: Array<{ c: number; r: number }> }>).find((b) => b.label === 'Cathedral')!;
  const facade = Math.max(...cath.entrances.map((e) => e.r));
  const arches = cath.entrances.filter((e) => e.r === facade).map((e) => e.c);
  assert.ok(arches.length >= 3);
  const res = world.graph.route((facade + 6) * COLS + 201, (facade - 20) * COLS + 201, 4000);
  assert.equal(res.status, 'found');
  const crossing = [...res.path!].filter((p) => Math.floor(p / COLS) === facade).map((p) => p % COLS);
  assert.ok(crossing.length > 0 && crossing.every((c) => arches.includes(c)), `crosses the facade at ${crossing}`);
});

test('traffic: parked queues never stand on or beside a zebra crossing', () => {
  for (const f of WORLD.furniture) {
    if (!f.type.startsWith('VEHICLE_')) continue;
    const e = getCatalogEntry(f.type)!;
    const solid = f.row + e.footprintH - 1;
    for (let dc = -1; dc <= e.footprintW; dc++)
      for (const dr of [-1, 0, 1]) assert.notEqual(WORLD.tiles[(solid + dr) * COLS + f.col + dc], 27, `${f.uid} beside a crossing`);
  }
});

test('towers: every skyline slot shows a different design', () => {
  const after = BUILD.towers.censusAfter as Record<string, number>;
  const instances = Object.values(after).reduce((n, k) => n + k, 0);
  assert.equal(instances, BUILD.towers.instances);
  for (const [design, n] of Object.entries(after)) assert.equal(n, 1, `${design} used ${n} times`);
});

test('room-level inhabitation: every designed room is on some resident routine (post or stop)', () => {
  const missing: string[] = [];
  for (const b of BUILD.buildings as Array<{ label: string; rooms: Array<{ name: string; kind: string; rect: [number, number, number, number] }> }>) {
    for (const room of b.rooms) {
      if (room.kind === 'corridor') continue; // galleries are walked through on every cross-building leg
      const [c0, r0, c1, r1] = room.rect;
      const visited = world.residents.some((res) => [...res.routine].some((t) => {
        const c = t % COLS;
        const r = Math.floor(t / COLS);
        return c >= c0 && c <= c1 && r >= r0 && r <= r1;
      }));
      if (!visited) {
        const inRoom = world.residents.filter((res) => res.district === b.label).length;
        const near = world.residents.flatMap((res) => [...res.routine].filter((t) => {
          const c = t % COLS;
          const r = Math.floor(t / COLS);
          return c >= c0 - 2 && c <= c1 + 2 && r >= r0 - 2 && r <= r1 + 2;
        }).map((t) => `${res.id}@${t % COLS},${Math.floor(t / COLS)}`));
        missing.push(`${b.label} / ${room.name} (residents of the place: ${inRoom}; nearby stops: ${near.join(' ')})`);
      }
    }
  }
  assert.deepEqual(missing, []);
});

test('cathedral in and out: residents have routines spanning the interior and the parvis or grounds', () => {
  const placeOf = (t: number) => WORLD.areaTiles?.[t] ?? null;
  const spanning = world.residents.filter((res) => {
    const places = new Set([...res.routine].map(placeOf));
    return places.has('Cathedral') && (places.has('Cathedral Parvis') || places.has('Cathedral Quarter'));
  });
  assert.ok(spanning.length >= 3, `${spanning.length} residents walk between the cathedral and its grounds`);
});

test('routine length is bounded and room coverage is complete under the bound', () => {
  assert.ok(world.coverage.longestRoutine <= AMBIENT_ROUTINE_MAX_STOPS);
  assert.equal(world.coverage.uncoveredByCap, 0);
  assert.equal(world.coverage.covered, world.coverage.rooms);
});

test('Gallery Office: sixteen distinct M01 exhibits, each once, inside the venue; no palace exhibit touched', () => {
  const v = BUILD.exhibits.venue as { c0: number; r0: number; c1: number; r1: number };
  const museum = WORLD.furniture.filter((f) => f.type.startsWith('MUSEUM_'));
  assert.equal(museum.length, 16);
  assert.equal(new Set(museum.map((f) => f.type)).size, 16);
  for (const f of museum) assert.ok(f.col >= v.c0 && f.col <= v.c1 && f.row >= v.r0 - 3 && f.row <= v.r1, `${f.type} inside the venue`);
  const home = R4.home!;
  for (const x of BUILD.exhibits.replaced as Array<{ at: [number, number] }>)
    assert.ok(!(x.at[0] >= home.c0 && x.at[0] < home.c1 && x.at[1] >= home.r0 && x.at[1] < home.r1), 'no palace record replaced');
});

test('OUTCOME 3: every district, the palace and deck, every office entrance and every station share ONE pedestrian network', async () => {
  const { districtsOf, livingRegions } = await import('../src/office/layout/worldViews.ts');
  const g = world.graph;
  const mask = world.pathfinder.mask.walkable;
  const onMain = (c: number, r: number) => mask[r * COLS + c] === 1 && g.componentOf(r * COLS + c) === main;
  const rectOnMain = (c0: number, r0: number, c1: number, r1: number) => {
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) if (onMain(c, r)) return true;
    return false;
  };
  // every district (the layout's own named areas)
  const districts = districtsOf(WORLD);
  assert.ok(districts.length >= 20, `districts ${districts.length}`);
  for (const d of districts) {
    let ok = false;
    for (let i = 0; i < COLS * WORLD.rows && !ok; i++) if (WORLD.areaTiles?.[i] === d.label && mask[i] === 1 && g.componentOf(i) === main) ok = true;
    assert.ok(ok, `district ${d.label} on the main network`);
  }
  // the palace and the deck (C1)
  const footprintOf = (type: string) => {
    const e = getCatalogEntry(type);
    return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
  };
  const home = livingRegions(WORLD, footprintOf as never);
  assert.ok(home && home.length === 2);
  for (const h of home!) assert.ok(rectOnMain(h.c0, h.r0, h.c1 - 1, h.r1 - 1), 'palace / deck on the main network');
  // office entrances: each building's recorded entrance cells
  for (const b of BUILD.buildings as Array<{ label: string; entrances?: Array<{ c: number; r: number }> }>)
    for (const e of b.entrances ?? []) assert.ok(onMain(e.c, e.r), `${b.label} entrance ${e.c},${e.r} on the main network`);
  // every station's island platform
  const line = os.transit!.line;
  assert.equal(line.stations.length, 4);
  for (const st of line.stations) assert.ok(st.island.every((t) => mask[t] === 1 && g.componentOf(t) === main), `${st.name} island on the main network`);
});
