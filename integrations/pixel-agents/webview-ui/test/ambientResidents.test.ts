/**
 * AMBIENT RESIDENTS and the WHOLE-WORLD PEDESTRIAN GRAPH on the SHIPPED metropolis world, judged by
 * the runtime's own walkability (tileMap.isWalkable, layoutSerializer.getBlockedTiles with the real
 * bundled catalogue) and through OfficeState exactly as the product builds them.
 *
 * Pins (S1):
 * - deterministic roster; ordinary population and hard cap; every requested job present;
 * - C1: the original palace and deck are inhabited at zero real agents; no stop is a seat or beside
 *   one; stops are walkable and dry;
 * - graph coverage: EVERY walkable tile is assigned to a hub, including pockets the lattice misses
 *   (synthetic two-pocket block + entrance case), and components count every walkable component;
 * - palace/deck/district reachability through the graph, with contiguous walkable routes;
 * - bounded search limits; per-tick search budget; leg-cache bound;
 * - residents move, and some cross into other places (whole-world walking);
 * - congestion: a head-on meeting in a corridor queues, then resolves (no gridlock);
 * - real agents win: a resident beside a seat yields when a real agent is assigned that seat, never
 *   stands on an agent's tile, and never wins hit-testing; residents never enter characters/seats;
 * - reduced motion holds residents still; labels always say Ambient and decorative.
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
import {
  AMBIENT_DECK_MIN,
  AMBIENT_LEG_CACHE_PER_RESIDENT,
  AMBIENT_PALACE_MIN,
  AMBIENT_PATH_MAX_EXPANSIONS,
  AMBIENT_PATH_SEARCHES_PER_TICK,
  AMBIENT_POPULATION_CAP,
  AMBIENT_POPULATION_ORDINARY,
  AMBIENT_ROUTE_MAX_LENGTH,
  WATER_FLOOR_TILES,
} from '../src/constants.ts';
import { PedestrianGraph } from '../src/office/ambient/ambientGraph.ts';
import {
  AMBIENT_JOBS,
  ambientLabel,
  REQUIRED_AMBIENT_JOBS,
} from '../src/office/ambient/ambientJobs.ts';
import { AmbientPathfinder } from '../src/office/ambient/ambientPath.ts';
import type { AmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { buildAmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { buildDynamicCatalog, getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import { districtsOf, livingRegions } from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLD: OfficeLayout = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'),
);
const COLS = WORLD.cols;

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

const footprintOf: FootprintOf = (type) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};

let os: OfficeState;
let world: AmbientWorld;

function rebuild(o: OfficeState, population?: number, seed?: number): AmbientWorld {
  return buildAmbientWorld({
    layout: o.layout,
    tileMap: o.tileMap,
    blockedTiles: o.blockedTiles,
    homeRegions: livingRegions(o.layout, footprintOf),
    seats: [...o.seats.values()],
    population,
    seed,
  });
}

function fingerprint(w: AmbientWorld): string {
  return JSON.stringify(
    w.residents.map((r) => [r.id, r.job, r.district, r.palette, r.hueShift, [...r.routine]]),
  );
}

const colRow = (i: number): [number, number] => [i % COLS, Math.floor(i / COLS)];

beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(WORLD);
  os.setAmbientEnabled(true);
  world = os.ambient!;
});

test('the roster is deterministic: same world and seed, same residents, posts and routines', () => {
  const again = new OfficeState(WORLD);
  again.setAmbientEnabled(true);
  assert.equal(fingerprint(again.ambient!), fingerprint(world));
  assert.notEqual(fingerprint(rebuild(os, undefined, 12345)), fingerprint(world), 'seed matters');
});

test('ordinary population is exactly the target plus the placed Easter-egg families (M3 S6); requests are clamped to the hard cap', () => {
  const placed = world.eggs.filter((e) => e.home !== null).length;
  assert.equal(world.residents.length, AMBIENT_POPULATION_ORDINARY + placed);
  assert.equal(world.residents.filter((r) => r.job.startsWith('egg-')).length, placed);
  // the cap is a hard total: at the cap no family is appended beyond it
  assert.equal(rebuild(os, AMBIENT_POPULATION_CAP * 10).residents.length, AMBIENT_POPULATION_CAP);
  assert.equal(rebuild(os, 0).residents.length, 0);
});

test('identities are unique and every requested job is present', () => {
  const ids = world.residents.map((r) => r.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const job of REQUIRED_AMBIENT_JOBS)
    assert.ok(world.residents.some((r) => r.job === job), `${job} present`);
});

test('C1: the original palace and deck are inhabited at zero real agents', () => {
  assert.equal(os.characters.size, 0, 'zero real agents');
  const home = livingRegions(WORLD, footprintOf);
  assert.ok(home && home.length === 2, 'the metropolis has a palace and a deck');
  const palace = world.residents.filter((r) => r.district === 'Palace');
  const deck = world.residents.filter((r) => r.district === 'Deck');
  assert.ok(palace.length >= AMBIENT_PALACE_MIN, `palace residents ${palace.length}`);
  assert.ok(deck.length >= AMBIENT_DECK_MIN, `deck residents ${deck.length}`);
  for (const r of palace) assert.equal(world.placeOf(r.routine[0]), 'Palace');
  for (const r of deck) assert.equal(world.placeOf(r.routine[0]), 'Deck');
});

test('every district, the palace, the deck and the forest are inhabited', () => {
  const places = new Set(world.residents.map((r) => r.district));
  for (const d of districtsOf(WORLD)) assert.ok(places.has(d.label), `${d.label} inhabited`);
  for (const p of ['Palace', 'Deck', 'Forest']) assert.ok(places.has(p), `${p} inhabited`);
});

test('stops are walkable, dry, never a seat or beside one', () => {
  // preconditions: there are residents with stops, and a non-empty seat zone to keep them out of
  assert.ok(world.residents.length > 0 && world.residents.every((r) => r.routine.length > 0));
  assert.ok(world.seatZone.size > 0, 'the seat zone is not empty');
  for (const r of world.residents) {
    for (const s of r.routine) {
      const [c, row] = colRow(s);
      assert.ok(isWalkable(c, row, os.tileMap, os.blockedTiles), `${r.id} stop walkable`);
      assert.ok(!WATER_FLOOR_TILES.has(os.tileMap[row][c]), `${r.id} stop dry`);
      assert.ok(!world.seatZone.has(s), `${r.id} stop is not a seat zone`);
    }
  }
});

test('graph coverage on the metropolis: every walkable tile is assigned to a hub', () => {
  const g = world.graph;
  assert.equal(g.stats.assignedTiles, g.stats.walkableTiles);
  const walkable = world.pathfinder.mask.walkable;
  for (let i = 0; i < walkable.length; i++)
    if (walkable[i] === 1) assert.ok(g.hubOf[i] >= 0, `tile ${i} assigned`);
  let sum = 0;
  for (const t of g.componentTiles) sum += t;
  assert.equal(sum, g.stats.walkableTiles, 'components account for every walkable tile');
});

test('reachability: palace, deck, forest and every district share the main component', () => {
  const g = world.graph;
  const main = g.componentOf(world.residents.find((r) => r.district === 'Metropolis')!.routine[0]);
  assert.ok(main >= 0);
  for (const r of world.residents)
    assert.equal(g.componentOf(r.routine[0]), main, `${r.id} home post reachable`);
});

test('a cross-world route from the palace to the harbour is contiguous and walkable', () => {
  const from = world.residents.find((r) => r.district === 'Palace')!.routine[0];
  const to = world.residents.find((r) => r.district === 'Harbour')!.routine[0];
  const route = world.findRoute(from, to);
  assert.ok(route.ok && !route.same, 'route found');
  assert.ok(route.path.length <= AMBIENT_ROUTE_MAX_LENGTH);
  let prev = from;
  for (const t of route.path) {
    const [pc, pr] = colRow(prev);
    const [c, r] = colRow(t);
    assert.equal(Math.abs(c - pc) + Math.abs(r - pr), 1, 'each step is one tile');
    assert.ok(isWalkable(c, r, os.tileMap, os.blockedTiles), 'each step walkable');
    prev = t;
  }
  assert.equal(prev, to);
});

test('graph seeding: two disconnected pockets in one lattice block both get hubs (entrance case)', () => {
  // 24 x 12 mask, spacing 12. Block (0,0) holds a large pocket (cols 0-7) whose tile wins the
  // block centre, and a small enclosed pocket (cols 10-11, rows 0-3) behind a wall column.
  const cols = 24;
  const rows = 12;
  const walkable = new Uint8Array(cols * rows);
  for (let r = 0; r < rows; r++) for (let c = 0; c <= 7; c++) walkable[r * cols + c] = 1;
  for (let r = 0; r <= 3; r++) for (let c = 10; c <= 11; c++) walkable[r * cols + c] = 1;
  const entrance = 3 * cols + 10; // an "entrance" inside the weaker pocket
  const g = new PedestrianGraph({ cols, rows, walkable }, 12);
  for (let i = 0; i < walkable.length; i++)
    if (walkable[i] === 1) assert.ok(g.hubOf[i] >= 0, `tile ${i} has a hub`);
  assert.equal(g.stats.assignedTiles, g.stats.walkableTiles);
  assert.equal(g.stats.pocketHubs, 1, 'the sweep added exactly one hub for the missed pocket');
  assert.equal(g.stats.components, 2, 'both pockets are counted');
  assert.deepEqual([...g.componentTiles].sort((a, b) => a - b), [8, 96]);
  assert.ok(g.componentOf(entrance) >= 0, 'the entrance is covered');
  assert.notEqual(g.componentOf(entrance), g.componentOf(0), 'and honestly separate');
  assert.equal(g.route(0, entrance, 1000).status, 'unreachable', 'reported, not dropped');
  assert.equal(g.route(10, entrance, 1000).status, 'found', 'routable inside its own pocket');
});

test('the bounded search stops at its expansion and length limits', () => {
  const pf = new AmbientPathfinder(world.pathfinder.mask);
  const a = world.residents.find((r) => r.district === 'Palace')!.routine[0];
  const b = world.residents.find((r) => r.district === 'Harbour')!.routine[0];
  const [ac, ar] = colRow(a);
  const [bc, br] = colRow(b);
  const tight = pf.search(ac, ar, bc, br, 50, 10_000);
  assert.equal(tight.status, 'budget');
  assert.ok(tight.expanded <= 51);
  assert.equal(pf.search(ac, ar, bc, br, 1_000_000, 20).status, 'too-long');
});

test('residents move, cross into other places, and respect the search budgets', () => {
  const o = new OfficeState(WORLD);
  o.setAmbientEnabled(true);
  const w = o.ambient!;
  const start = w.residents.map((r) => `${r.tileCol},${r.tileRow}`);
  let prev = 0;
  for (let i = 0; i < 3000; i++) {
    o.update(0.05);
    assert.ok(w.stats.searches - prev <= AMBIENT_PATH_SEARCHES_PER_TICK, 'per-tick search budget');
    prev = w.stats.searches;
    if (i % 500 === 0)
      for (const r of w.residents)
        assert.ok(isWalkable(r.tileCol, r.tileRow, o.tileMap, o.blockedTiles), `${r.id} walkable`);
  }
  const moved = w.residents.filter((r, i) => `${r.tileCol},${r.tileRow}` !== start[i]).length;
  const crossed = w.residents.filter((r) => r.placesVisited.size >= 2).length;
  assert.ok(moved >= w.residents.length / 2, `moved ${moved}/${w.residents.length}`);
  assert.ok(crossed >= 10, `residents that entered another place: ${crossed}`);
  assert.ok(w.stats.graphRoutes > 0 && w.stats.localRoutes > 0, 'both route kinds used');
  assert.ok(w.stats.maxExpanded <= AMBIENT_PATH_MAX_EXPANSIONS + 1, 'expansion bound held');
  for (const r of w.residents) assert.ok(r.legCache.size <= AMBIENT_LEG_CACHE_PER_RESIDENT);
});

test('congestion: a head-on meeting in a one-tile corridor queues, then resolves', () => {
  // 40 x 3 corridor (row 1 walkable), two residents walking at each other.
  const cols = 40;
  const rows = 3;
  const tileMap = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, () => (r === 1 ? TileType.FLOOR_1 : TileType.WALL)),
  );
  const w = buildAmbientWorld({
    layout: { cols, rows },
    tileMap,
    blockedTiles: new Set(),
    homeRegions: null,
    population: 2,
  });
  assert.equal(w.residents.length, 2);
  const [a, b] = w.residents;
  const place = (r: typeof a, c: number): void => {
    r.tileCol = c;
    r.tileRow = 1;
    r.x = c * 16 + 8;
    r.y = 24;
  };
  // Re-seat both, rebuild occupancy through a fresh world with the same residents.
  place(a, 5);
  place(b, 30);
  const fresh = new (w.constructor as new (...args: unknown[]) => AmbientWorld)(
    w.residents,
    w.pathfinder,
    w.graph,
    w.seed,
    w.seatZone,
    new Map(),
    w.placeOf,
  );
  a.route = Int32Array.from({ length: 30 }, (_, k) => cols + 6 + k); // to col 35
  b.route = Int32Array.from({ length: 30 }, (_, k) => cols + 29 - k); // to col 0
  for (const r of [a, b]) {
    r.routePos = 0;
    r.moveProgress = 0;
    r.state = 'walk';
  }
  for (let i = 0; i < 2000 && (a.state === 'walk' || b.state === 'walk'); i++) fresh.update(0.05);
  assert.ok(fresh.stats.congestionWaits >= 1, 'they queued');
  assert.ok(fresh.stats.passThroughs >= 1, 'then one passed: no permanent gridlock');
  assert.equal(a.tileCol, 35);
  assert.equal(b.tileCol, 0);
});

/** A resident parked (dwelling 100 s) on a walkable 4-neighbour of a free seat: the conflict, built. */
function residentBesideFreeSeat(o: OfficeState) {
  const w = o.ambient!;
  const [uid, seat] = [...o.seats].find(([, s]) => !s.assigned)!;
  assert.ok(uid, 'precondition: a free seat exists');
  const neighbour = [
    [0, -1],
    [0, 1],
    [-1, 0],
    [1, 0],
  ]
    .map(([dc, dr]) => [seat.seatCol + dc, seat.seatRow + dr])
    .find(([c, r]) => isWalkable(c, r, o.tileMap, o.blockedTiles));
  assert.ok(neighbour, 'precondition: the seat has a walkable 4-neighbour');
  const r = w.residents.find((x) => x.district === 'Palace');
  assert.ok(r, 'precondition: a Palace resident exists');
  r.tileCol = neighbour[0];
  r.tileRow = neighbour[1];
  r.x = r.tileCol * 16 + 8;
  r.y = r.tileRow * 16 + 8;
  r.state = 'dwell';
  r.timer = 100;
  const zone = w.priorityTiles({ agentTiles: new Set(), assignedSeats: [uid] });
  assert.ok(zone.has(r.tileRow * COLS + r.tileCol), 'precondition: the resident stands IN that seat\'s zone');
  return { w, uid, r, zone, tile: r.tileRow * COLS + r.tileCol };
}

test('control: with NO agent assigned, the parked resident stays where it is (nothing else moves it)', () => {
  const o = new OfficeState(WORLD);
  o.setAmbientEnabled(true);
  const { w, r, tile } = residentBesideFreeSeat(o);
  const before = w.stats.agentYields;
  for (let i = 0; i < 60; i++) o.update(0.05);
  assert.equal(r.tileRow * COLS + r.tileCol, tile, 'still on its tile');
  assert.equal(r.state, 'dwell');
  assert.equal(w.stats.agentYields, before, 'no yield without an agent');
});

test('real agents win: a resident beside a seat yields when a real agent is assigned it', () => {
  const o = new OfficeState(WORLD);
  o.setAmbientEnabled(true);
  const { w, uid, r, zone, tile } = residentBesideFreeSeat(o);
  o.addAgent(1, undefined, undefined, uid, true);
  assert.equal(o.characters.get(1)?.seatId, uid, 'the real agent has the seat');
  const before = w.stats.agentYields;
  for (let i = 0; i < 60; i++) o.update(0.05);
  assert.ok(w.stats.agentYields > before, 'a yield was counted');
  assert.notEqual(r.tileRow * COLS + r.tileCol, tile, 'THIS resident left its tile');
  assert.ok(!zone.has(r.tileRow * COLS + r.tileCol), 'it no longer stands in the seat zone');
  assert.equal(o.characters.get(1)?.seatId, uid, 'the agent keeps its seat');
  assert.equal(o.getPersistableSeats()[1]?.seatId, uid, 'only the agent is persisted');
});

test('reduced motion holds MOTION, never PRIORITY: the parked resident yields to the arriving agent in BOTH modes, with equal yield counts', () => {
  const run = (reduced: boolean) => {
    const o = new OfficeState(WORLD);
    o.setAmbientEnabled(true);
    o.reducedMotion = reduced;
    const { w, uid, r, zone, tile } = residentBesideFreeSeat(o);
    const othersAt = () =>
      w.residents
        .filter((x) => x !== r)
        .map((x) => `${x.id}@${x.x},${x.y}`)
        .join('|');
    const others0 = othersAt();
    o.addAgent(1, undefined, undefined, uid, true);
    assert.equal(o.characters.get(1)?.seatId, uid, 'precondition: the real agent has the seat');
    const before = w.stats.agentYields;
    for (let i = 0; i < 60; i++) o.update(0.05);
    const now = r.tileRow * COLS + r.tileCol;
    return { yields: w.stats.agentYields - before, left: now !== tile && !zone.has(now), othersStill: othersAt() === others0 };
  };
  const normal = run(false);
  const reduced = run(true);
  assert.ok(normal.yields >= 1 && normal.left, `normal motion: it yields and leaves the zone (${JSON.stringify(normal)})`);
  assert.ok(reduced.yields >= 1 && reduced.left, `reduced motion: it STILL yields and leaves the zone (${JSON.stringify(reduced)})`);
  assert.equal(reduced.yields, normal.yields, 'the same yield count in both modes');
  assert.ok(reduced.othersStill, 'reduced motion: every other resident held still (motion is held, priority is not)');
});

test('a walking resident never steps onto a real agent\'s tile: it waits, then re-plans', () => {
  // 40 x 3 corridor (row 1 walkable); a resident routed straight along it, a real agent's tile ahead
  const cols = 40;
  const tileMap = Array.from({ length: 3 }, (_, r) => Array.from({ length: cols }, () => (r === 1 ? TileType.FLOOR_1 : TileType.WALL)));
  const w = buildAmbientWorld({ layout: { cols, rows: 3 }, tileMap, blockedTiles: new Set(), homeRegions: null, population: 1 });
  assert.equal(w.residents.length, 1, 'precondition: one resident');
  const [r] = w.residents;
  r.tileCol = 5;
  r.tileRow = 1;
  r.x = 5 * 16 + 8;
  r.y = 24;
  r.route = Int32Array.from({ length: 20 }, (_, k) => cols + 6 + k); // to col 25
  r.routePos = 0;
  r.moveProgress = 0;
  r.state = 'walk';
  const agentTile = cols + 10; // col 10, on the route
  assert.ok([...r.route].includes(agentTile), 'precondition: the agent stands ON the resident\'s route');
  const ctx = { agentTiles: new Set([agentTile]), assignedSeats: [] };
  const replans0 = w.stats.agentBlockReplans;
  let maxCol = r.tileCol;
  for (let i = 0; i < 400; i++) {
    w.update(0.05, ctx);
    assert.notEqual(r.tileRow * cols + r.tileCol, agentTile, 'never on the agent\'s tile');
    if (r.route.length && r.routePos < r.route.length) assert.ok(!(r.moveProgress > 0 && r.route[r.routePos] === agentTile), 'never mid-step onto it');
    maxCol = Math.max(maxCol, r.tileCol);
  }
  assert.equal(maxCol, 9, 'it walked up to the tile before the agent, and no further');
  assert.ok(w.stats.agentBlockReplans > replans0, 'it re-planned instead of walking through');
});

test('residents never become agents and never win hit-testing over one', () => {
  const o = new OfficeState(WORLD);
  assert.equal(o.ambient, null, 'disabled by default (normal Pixel never enables it)');
  o.setAmbientEnabled(true);
  assert.equal(o.characters.size, 0);
  assert.equal(o.getCharacters().length, 0);
  assert.deepEqual(o.getPersistableSeats(), {});
  o.addAgent(7, undefined, undefined, undefined, true);
  const agent = o.characters.get(7)!;
  const r = o.getAmbientResidents()[0];
  r.x = agent.x;
  r.y = agent.y;
  assert.equal(o.getCharacterAt(agent.x, agent.y - 4), 7, 'the agent is hit');
  assert.ok(o.getAmbientAt(agent.x, agent.y - 4), 'the resident is there too');
  assert.ok(!o.characters.has(-1) && o.getCharacters().every((c) => typeof c.id === 'number'));
  o.setAmbientEnabled(false);
  assert.equal(o.getAmbientResidents().length, 0);
});

test('reduced motion: residents hold still', () => {
  const o = new OfficeState(WORLD);
  o.setAmbientEnabled(true);
  o.reducedMotion = true;
  const before = o.getAmbientResidents().map((r) => `${r.x},${r.y}`);
  for (let i = 0; i < 400; i++) o.update(0.05);
  assert.deepEqual(
    o.getAmbientResidents().map((r) => `${r.x},${r.y}`),
    before,
  );
});

test('labels always say Ambient and decorative, for every job', () => {
  for (const job of Object.keys(AMBIENT_JOBS) as Array<keyof typeof AMBIENT_JOBS>) {
    const label = ambientLabel(job);
    assert.ok(label.startsWith('Ambient · '), label);
    assert.ok(label.endsWith(' · decorative'), label);
    assert.ok(!/mission|agent|working on/i.test(label), `no agent/Mission wording: ${label}`);
  }
});
