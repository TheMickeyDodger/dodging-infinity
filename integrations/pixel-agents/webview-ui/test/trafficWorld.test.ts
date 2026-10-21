/**
 * LIVING CITY 6–9, moving traffic on the SHIPPED living metropolis, judged by the product's own code: the lanes the
 * engine derives from its roads, the vehicles it brings to life, the audit of what they hold over ten simulated
 * minutes, and the real OfficeState around it: the layout and blocked tiles untouched, 'off' restoring the world
 * exactly, the DI-only gate, a real agent's priority on a real road, and reduced motion.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { CityPresentation } from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { getBlockedTiles } from '../src/office/layout/layoutSerializer.ts';
import { planTraffic } from '../src/office/traffic/trafficBuild.ts';
import { HEADING_E, HEADING_W, KIND_ZEBRA } from '../src/office/traffic/trafficGraph.ts';
import { vehiclePlacement } from '../src/office/traffic/trafficRender.ts';
import { NO_PEOPLE, TrafficWorld } from '../src/office/traffic/trafficWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
let layout: OfficeLayout;
beforeAll(() => {
  loadBundledCatalog();
  layout = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
}, 120_000);

const present = (over: Partial<CityPresentation>): CityPresentation => ({
  v: 1,
  lighting: 'day',
  weather: 'off',
  traffic: 'normal',
  ...over,
});
const ROAD_TILES = new Set<number>([15, 16, 17, 27]);

// ── the derived network ───────────────────────────────────────────────────────

test('the shipped roads yield 17 bands, 12 working intersections and bounded lanes, settled in a few passes', () => {
  const plan = planTraffic(layout, getCatalogEntry, getBlockedTiles);
  assert.equal(plan.settled, true);
  assert.ok(plan.passes <= 3, `passes ${plan.passes}`);
  assert.equal(plan.graph.bands.length, 17);
  assert.equal(plan.graph.bands.filter((b) => b.horizontal).length, 9);
  assert.equal(plan.graph.boxes.length, 12);
  assert.ok(
    plan.graph.lanes.length > 90 && plan.graph.lanes.length <= C.TRAFFIC_MAX_LANES,
    `lanes ${plan.graph.lanes.length}`,
  );
  assert.ok(plan.graph.boxes.length <= C.TRAFFIC_MAX_BOXES);
  // the four row-39 intersections each have a parked taxi standing in the box: they are NOT intersections
  const centres = plan.graph.boxes.map((b) => `${b.cx},${b.cy}`);
  for (const c of [185, 217, 249, 281]) {
    assert.ok(!centres.includes(`${c},39`), `${c},39 has a parked taxi in it`);
    for (const r of [11, 67, 95])
      assert.ok(centres.includes(`${c},${r}`), `${c},${r} should be an intersection`);
  }
});

test('every lane tile is road and unblocked, every box tile too, and zebra kinds are zebra tiles', () => {
  const plan = planTraffic(layout, getCatalogEntry, getBlockedTiles);
  const homeUids = new Set(plan.homes.map((h) => h.uid));
  const blocked = getBlockedTiles(layout.furniture.filter((f) => !homeUids.has(f.uid)));
  const { cols } = layout;
  let tiles = 0;
  for (const lane of plan.graph.lanes) {
    for (let i = 0; i < lane.tiles.length; i++) {
      const t = lane.tiles[i];
      tiles++;
      assert.ok(
        ROAD_TILES.has(layout.tiles[t]),
        `lane ${lane.id} tile ${t} is type ${layout.tiles[t]}`,
      );
      assert.ok(
        !blocked.has(`${t % cols},${Math.floor(t / cols)}`),
        `lane ${lane.id} tile ${t} is blocked by furniture`,
      );
      if (lane.kind[i] === KIND_ZEBRA) assert.equal(layout.tiles[t], 27);
      // consecutive tiles are neighbours in the lane's own direction
      if (i > 0) {
        const d = Math.abs(lane.tiles[i] - lane.tiles[i - 1]);
        assert.ok(d === 1 || d === cols, `lane ${lane.id} jumps at ${i}`);
      }
    }
  }
  assert.ok(tiles > 2000, `${tiles} free lane tiles`);
  for (const t of plan.graph.boxTiles.keys())
    if (plan.graph.boxTiles[t]) assert.ok(!blocked.has(`${t % cols},${Math.floor(t / cols)}`));
});

test("the world's own lane-aligned vehicles are the traffic: 68 homes, their kinds, their places and their art", () => {
  const plan = planTraffic(layout, getCatalogEntry, getBlockedTiles);
  assert.equal(plan.homes.length, 68);
  const kinds: Record<string, number> = {};
  for (const h of plan.homes) kinds[h.kind] = (kinds[h.kind] ?? 0) + 1;
  assert.deepEqual(kinds, {
    TAXI: 39,
    HATCHBACK_RED: 7,
    SEDAN_BLUE: 8,
    POLICE_CRUISER: 5,
    SUV_SILVER: 5,
    FIRE_ENGINE: 2,
    AMBULANCE: 2,
  });
  const byUid = new Map(layout.furniture.map((f) => [f.uid, f]));
  for (const h of plan.homes) {
    assert.ok(h.heading === HEADING_E || h.heading === HEADING_W);
    assert.ok(h.body >= C.TRAFFIC_BODY_MIN_TILES && h.body <= C.TRAFFIC_BODY_MAX_TILES);
    const f = byUid.get(h.uid)!;
    assert.match(f.type, h.heading === HEADING_E ? /_RIGHT$/ : /_LEFT$/);
    // its body is exactly the furniture's own ground tiles
    const lane = plan.graph.lanes[h.lane];
    const body = new Set<number>();
    for (let k = 0; k < h.body; k++) body.add(lane.tiles[h.idx - k]);
    const entry = getCatalogEntry(f.type)!;
    const row = f.row + (entry.backgroundTiles ?? 0);
    for (let dc = 0; dc < entry.footprintW; dc++)
      assert.ok(body.has(row * layout.cols + f.col + dc), `${f.type} ${f.uid}`);
  }
  // nothing but real vehicle furniture on a street was animated: the parked lots' vehicles, facing up or down, stay parked
  assert.ok(layout.furniture.filter((f) => /^VEHICLE_/.test(f.type)).length > plan.homes.length);
});

test('a vehicle is drawn exactly where its furniture is: pose to pixels reproduces the placed art, piece for piece', () => {
  const plan = planTraffic(layout, getCatalogEntry, getBlockedTiles);
  const w = new TrafficWorld(plan.graph, plan.homes);
  w.setCap(C.TRAFFIC_MAX_VEHICLES);
  w.update(0.1, NO_PEOPLE, true); // reduced motion shows every vehicle at its home place at once
  const byUid = new Map(layout.furniture.map((f) => [f.uid, f]));
  let checked = 0;
  for (const v of w.vehicles) {
    if (v.state !== 'drive') continue; // a home whose neighbour overlaps it waits; the rest are all here
    const f = byUid.get(v.homeUid)!;
    const entry = getCatalogEntry(f.type)!;
    const sprite = Array.from({ length: entry.footprintH * 16 }, () =>
      new Array(entry.footprintW * 16).fill(''),
    );
    const p = vehiclePlacement(v, sprite);
    assert.equal(p.x, f.col * 16, `${f.type} x`);
    assert.equal(p.y, f.row * 16, `${f.type} y`);
    assert.equal(p.zY, (f.row + entry.footprintH) * 16, `${f.type} depth key`);
    checked++;
  }
  assert.ok(checked >= 60, `only ${checked} vehicles shown at home`);
});

// ── ten simulated minutes ─────────────────────────────────────────────────────

test('over ten simulated minutes: vehicles drive, stop, queue, turn and never share a tile or leave the road', () => {
  const plan = planTraffic(layout, getCatalogEntry, getBlockedTiles);
  const w = new TrafficWorld(plan.graph, plan.homes);
  w.setCap(C.TRAFFIC_MAX_VEHICLES);
  const laneTiles = new Set<number>();
  for (const l of plan.graph.lanes) for (const t of l.tiles) laneTiles.add(t);
  let shared = 0;
  let off = 0;
  let routeMax = 0;
  const stoppedAtLine = new Set<number>();
  const drove = new Set<number>();
  const turned = new Set<number>();
  const lastHeading = new Map<number, number>();
  for (let k = 0; k < 6000; k++) {
    w.update(0.1, NO_PEOPLE, false);
    const seen = new Map<number, number>();
    for (const v of w.vehicles) {
      routeMax = Math.max(routeMax, v.route.length);
      if (v.state === 'stop' && v.v < 0.25) stoppedAtLine.add(v.slot);
      if (v.v > 1) drove.add(v.slot);
      const before = lastHeading.get(v.slot);
      if (before !== undefined && before !== v.heading && v.state !== 'in' && v.state !== 'gone')
        turned.add(v.slot);
      lastHeading.set(v.slot, v.heading);
      for (const t of w.heldTiles(v)) {
        if (seen.has(t) && seen.get(t) !== v.slot) shared++;
        seen.set(t, v.slot);
        if (w.holder(t) !== v) shared++;
        if (!laneTiles.has(t)) off++;
      }
    }
  }
  assert.equal(shared, 0, 'a tile was claimed twice');
  assert.equal(off, 0, 'a vehicle held a tile that is not a lane tile');
  assert.ok(routeMax <= C.TRAFFIC_ROUTE_MAX_TILES, `route ${routeMax}`);
  assert.ok(w.counters.tilesDriven > 20_000, `drove ${w.counters.tilesDriven} tiles`);
  assert.ok(w.counters.stops > 500, `${w.counters.stops} stop-line entries`);
  assert.ok(w.counters.maxQueue >= 3, `the longest queue was ${w.counters.maxQueue}`);
  assert.ok(w.counters.vehicleDenials > 0, 'no vehicle ever waited for another');
  assert.ok(drove.size >= 60, `only ${drove.size} of 68 ever drove`);
  assert.ok(
    stoppedAtLine.size >= 20,
    `only ${stoppedAtLine.size} vehicles ever waited at a stop line`,
  );
  assert.ok(turned.size >= 20, `only ${turned.size} vehicles ever turned`);
  assert.equal(w.counters.yields, 0, 'nobody walked into traffic, so nothing yielded');
  assert.equal(
    w.counters.watchdogs,
    0,
    'no vehicle sat for 30 seconds: the gridlock valve never opened',
  );
  // every kind drove, the emergency-looking ones among them, exactly as everyone else
  const driven = new Set(w.vehicles.filter((v) => drove.has(v.slot)).map((v) => v.kind));
  for (const k of [
    'TAXI',
    'SEDAN_BLUE',
    'HATCHBACK_RED',
    'SUV_SILVER',
    'POLICE_CRUISER',
    'FIRE_ENGINE',
    'AMBULANCE',
  ])
    assert.ok(driven.has(k), `${k} never drove`);
}, 120_000);

// ── the real OfficeState around it ────────────────────────────────────────────

const newOffice = (di = true): OfficeState => {
  const os = new OfficeState(structuredClone(layout));
  if (di) os.setAmbientEnabled(true);
  return os;
};

test('traffic leaves the layout and the walkable world exactly as they were, and off restores the furniture exactly', () => {
  const os = newOffice();
  const layoutBefore = JSON.stringify(os.getLayout());
  const blockedBefore = [...os.blockedTiles].sort().join('|');
  const walkableBefore = os.walkableTiles.length;
  os.setCityPresentation(present({ traffic: 'off' }));
  const furnitureOff = os.furniture.length;
  os.setCityPresentation(present({ traffic: 'normal' }));
  const traffic = os.getTraffic()!;
  assert.equal(traffic.vehicles.length, 68);
  assert.equal(traffic.hiddenUids().size, 68);
  assert.equal(
    os.furniture.length,
    furnitureOff - 68,
    "each driving vehicle's static copy is skipped, and nothing else",
  );
  // the layout, the blocked tiles and the walkable tiles are untouched by it
  assert.equal(JSON.stringify(os.getLayout()), layoutBefore);
  assert.equal([...os.blockedTiles].sort().join('|'), blockedBefore);
  assert.equal(os.walkableTiles.length, walkableBefore);
  os.setCityPresentation(present({ traffic: 'low' }));
  assert.equal(os.getTraffic()!.hiddenUids().size, C.TRAFFIC_CAP_LOW);
  assert.equal(os.furniture.length, furnitureOff - C.TRAFFIC_CAP_LOW);
  os.setCityPresentation(present({ traffic: 'off' }));
  assert.equal(os.furniture.length, furnitureOff, 'off is the world as it was');
  assert.deepEqual([...os.getVehicles()], []);
  assert.equal(JSON.stringify(os.getLayout()), layoutBefore);
}, 120_000);

test("the traffic is the DI office's only: ordinary Pixel never builds it", () => {
  const os = newOffice(false);
  os.setCityPresentation(present({ traffic: 'normal' }));
  assert.equal(os.getTraffic(), null);
  assert.deepEqual([...os.getVehicles()], []);
  assert.equal(
    os.furniture.length,
    layout.furniture.length - layout.furniture.filter((f) => !getCatalogEntry(f.type)).length,
  );
}, 60_000);

test('replacing the layout rebuilds the traffic from the new one: same ids, nothing duplicated', () => {
  const os = newOffice();
  const first = os.getTraffic()!;
  const ids = first.vehicles.map((v) => v.id);
  os.rebuildFromLayout(structuredClone(layout));
  const second = os.getTraffic()!;
  assert.notEqual(second, first);
  assert.deepEqual(
    second.vehicles.map((v) => v.id),
    ids,
  );
  assert.equal(new Set(second.vehicles.map((v) => v.id)).size, 68);
  assert.equal(second.hiddenUids().size, 68);
}, 120_000);

test('a real agent on a real road is never driven through, never blocked, and traffic still flows around the rest', () => {
  const os = newOffice();
  os.addAgent(1, 0, 0, undefined, true);
  const agent = os.characters.get(1)!;
  const blockedBefore = [...os.blockedTiles].sort().join('|');
  const world = os.getTraffic()!;
  // let the street fill, then stand the agent on a lane tile just ahead of a vehicle that is driving
  for (let k = 0; k < 300; k++) os.update(0.1);
  const v = world.vehicles.find((x) => x.state === 'drive' && x.v > 2)!;
  assert.ok(v, 'a vehicle should be driving');
  const tile = v.route[Math.min(v.route.length - 1, Math.ceil(v.head) + 3)];
  const col = tile % layout.cols;
  const row = Math.floor(tile / layout.cols);
  let violations = 0;
  for (let k = 0; k < 400; k++) {
    // hold the agent there (it is a real character: nothing here steers it, the test only places it)
    agent.tileCol = col;
    agent.tileRow = row;
    agent.x = col * 16 + 8;
    agent.y = row * 16 + 8;
    agent.path = [];
    os.update(0.1);
    for (const w of world.vehicles) {
      for (const t of world.heldTiles(w)) {
        const dx = Math.abs((t % layout.cols) - col);
        const dy = Math.abs(Math.floor(t / layout.cols) - row);
        if (dx <= 1 && dy <= 1) violations++;
      }
    }
  }
  assert.equal(violations, 0, 'a vehicle held a tile within one of a real agent');
  assert.ok(
    world.counters.pedestrianDenials > 0,
    'the agent must actually have held a vehicle back',
  );
  assert.equal(
    [...os.blockedTiles].sort().join('|'),
    blockedBefore,
    'decoration never changes what a real agent can walk on',
  );
}, 120_000);

test('reduced motion: the street is shown as it was parked, held still, and the preference turned on later freezes it', () => {
  const os = newOffice();
  os.reducedMotion = true;
  os.setCityPresentation(present({ traffic: 'normal' }));
  os.update(0.1);
  const world = os.getTraffic()!;
  const frozen = () =>
    JSON.stringify(world.vehicles.map((v) => [v.state, v.fade, v.head, v.route]));
  const shown = world.vehicles.filter((v) => v.state === 'drive').length;
  assert.ok(shown >= 60, `${shown} vehicles shown`);
  const before = frozen();
  for (let k = 0; k < 200; k++) os.update(0.1);
  assert.equal(frozen(), before);
  assert.equal(world.counters.tilesDriven, 0);
  // moving again once the preference is off
  os.reducedMotion = false;
  for (let k = 0; k < 200; k++) os.update(0.1);
  assert.ok(world.counters.tilesDriven > 0);
}, 120_000);

test('traffic is decoration: identical worlds stay identical whatever the streets are doing', () => {
  const plain = newOffice();
  const busy = newOffice();
  plain.setCityPresentation(present({ traffic: 'off' }));
  busy.setCityPresentation(present({ traffic: 'normal' }));
  const seat = [...plain.seats.keys()][0];
  for (const o of [plain, busy]) o.addAgent(1, 2, 0, seat, true);
  for (let k = 0; k < 600; k++) {
    plain.update(0.1);
    busy.update(0.1);
  }
  assert.equal(
    JSON.stringify(busy.serializeAmbient(0)),
    JSON.stringify(plain.serializeAmbient(0)),
    'residents and trains must not notice the traffic',
  );
  assert.equal(JSON.stringify(busy.getTrains()), JSON.stringify(plain.getTrains()));
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
        ]),
    );
  assert.equal(key(busy), key(plain));
  assert.ok(busy.getTraffic()!.counters.tilesDriven > 1000, 'and the traffic really did drive');
  assert.equal(plain.getTraffic()!.counters.tilesDriven, 0);
}, 180_000);
