/**
 * LIVING CITY 6–9, moving traffic: the REAL engine (road graph, claims, stops, priority, reduced motion) on a small
 * synthetic plus-shaped world, where every tile can be reasoned about. The shipped metropolis is judged separately
 * (trafficWorld.test.ts). Nothing here is a mock of the engine: the graph builder, the claim and stop logic and the
 * pedestrian rules are the product's own, driven step by step.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import * as C from '../src/constants.ts';
import type { TrafficGraph } from '../src/office/traffic/trafficGraph.ts';
import {
  HEADING_E,
  HEADING_N,
  HEADING_S,
  HEADING_W,
  indexOf,
  KIND_BOX,
  KIND_ROAD,
  KIND_ZEBRA,
  laneOf,
} from '../src/office/traffic/trafficGraph.ts';
import type { TrafficPeople, Vehicle, VehicleHome } from '../src/office/traffic/trafficWorld.ts';
import { NO_PEOPLE, TrafficWorld, vehicleType } from '../src/office/traffic/trafficWorld.ts';
import { PLUS_COLS, plusGraph, tileOf } from './helpers/plusWorld.ts';

const home = (
  g: TrafficGraph,
  uid: string,
  heading: number,
  col: number,
  row: number,
  kind = 'TAXI',
  body = 3,
): VehicleHome => {
  const code = g.laneAt(tileOf(col, row), heading);
  assert.ok(code >= 0, `no lane at ${col},${row} heading ${heading}`);
  return { uid, kind, heading, lane: laneOf(code), idx: indexOf(code), body };
};
const world = (g: TrafficGraph, homes: VehicleHome[]): TrafficWorld => {
  const w = new TrafficWorld(g, homes);
  w.setCap(homes.length);
  return w;
};
const people = (p: Partial<TrafficPeople>): TrafficPeople => ({ ...NO_PEOPLE, ...p });
const byUid = (w: TrafficWorld, uid: string): Vehicle => w.vehicles.find((v) => v.homeUid === uid)!;
/** Step the world `seconds` at `dt`, calling `each(timeSoFar)` after every step. */
function run(
  w: TrafficWorld,
  seconds: number,
  dt = 0.05,
  ppl: TrafficPeople = NO_PEOPLE,
  reduced = false,
  each?: (t: number) => void,
): void {
  const n = Math.round(seconds / dt);
  for (let k = 1; k <= n; k++) {
    w.update(dt, ppl, reduced);
    each?.(k * dt);
  }
}
const col = (v: Vehicle): number => v.hx;
/** How many tiles are claimed by two vehicles at once, or claimed by one while the occupancy map says another: both are
 *  failures of the one rule that matters, and this reads each vehicle's OWN claim so neither can hide. */
function auditClaims(w: TrafficWorld): number {
  const seen = new Map<number, number>();
  let bad = 0;
  for (const v of w.vehicles) {
    for (const t of w.heldTiles(v)) {
      if (seen.has(t) && seen.get(t) !== v.slot) bad++;
      seen.set(t, v.slot);
      if (w.holder(t) !== v) bad++;
    }
  }
  return bad;
}

// ── the bounds, exactly ───────────────────────────────────────────────────────

test('every traffic bound is exactly this value', () => {
  assert.equal(C.TRAFFIC_SEED, 0x7a11c);
  assert.equal(C.TRAFFIC_MAX_VEHICLES, 96);
  assert.equal(C.TRAFFIC_CAP_LOW, 24);
  assert.equal(C.TRAFFIC_MAX_LANES, 256);
  assert.equal(C.TRAFFIC_MAX_BOXES, 64);
  assert.equal(C.TRAFFIC_LANE_MIN_TILES, 6);
  assert.equal(C.TRAFFIC_BODY_MIN_TILES, 2);
  assert.equal(C.TRAFFIC_BODY_MAX_TILES, 4);
  assert.equal(C.TRAFFIC_ROUTE_MAX_TILES, 96);
  assert.equal(C.TRAFFIC_ROUTE_LOOKAHEAD_TILES, 8);
  assert.equal(C.TRAFFIC_CLAIM_AHEAD_TILES, 2);
  assert.equal(C.TRAFFIC_SPEED_MIN, 2.8);
  assert.equal(C.TRAFFIC_SPEED_MAX, 4.4);
  assert.equal(C.TRAFFIC_ACCEL, 5);
  assert.equal(C.TRAFFIC_BRAKE, 9);
  assert.equal(C.TRAFFIC_STOP_DWELL_SEC, 0.7);
  assert.equal(C.TRAFFIC_STOP_JITTER_SEC, 0.6);
  assert.equal(C.TRAFFIC_END_DWELL_SEC, 1);
  assert.equal(C.TRAFFIC_FADE_SEC, 0.5);
  assert.equal(C.TRAFFIC_RESPAWN_SEC, 1.5);
  assert.equal(C.TRAFFIC_STUCK_SEC, 30);
  assert.equal(C.TRAFFIC_TURN_WEIGHT_STRAIGHT, 6);
  assert.equal(C.TRAFFIC_TURN_WEIGHT_RIGHT, 3);
  assert.equal(C.TRAFFIC_TURN_WEIGHT_LEFT, 1);
  assert.equal(C.TRAFFIC_PEDESTRIAN_LOOKAHEAD_TILES, 3);
  assert.equal(C.TRAFFIC_AGENT_HALO_TILES, 1);
  assert.equal(C.TRAFFIC_START_STAGGER_SEC, 2);
  assert.equal(C.TRAFFIC_LABEL, 'Decorative traffic');
});

// ── the graph ─────────────────────────────────────────────────────────────────

test('the plus world yields two bands, one intersection and four full lanes with their zones marked', () => {
  const g = plusGraph();
  assert.equal(g.bands.length, 2);
  assert.deepEqual(g.boxes, [{ cx: 12, cy: 12 }]);
  assert.equal(g.lanes.length, 4);
  const east = g.lanes[laneOf(g.laneAt(tileOf(0, 13), HEADING_E))];
  assert.equal(east.tiles.length, PLUS_COLS);
  for (let i = 0; i < PLUS_COLS; i++) assert.equal(east.tiles[i], tileOf(i, 13));
  const kinds = Array.from(east.kind);
  assert.deepEqual(
    kinds.map((k, i) => (k === KIND_ZEBRA ? i : -1)).filter((i) => i >= 0),
    [7, 8],
  );
  assert.deepEqual(
    kinds.map((k, i) => (k === KIND_BOX ? i : -1)).filter((i) => i >= 0),
    [11, 12, 13],
  );
  // right-hand traffic: the four lanes and where they are
  assert.ok(g.laneAt(tileOf(5, 11), HEADING_W) >= 0);
  assert.ok(g.laneAt(tileOf(11, 5), HEADING_S) >= 0);
  assert.ok(g.laneAt(tileOf(13, 5), HEADING_N) >= 0);
  assert.equal(g.laneAt(tileOf(5, 13), HEADING_W), -1); // the wrong direction for that lane
  assert.equal(g.laneAt(tileOf(5, 12), HEADING_E), -1); // the centre line is no lane
  assert.equal(
    g.boxTiles.reduce((a, b) => a + b, 0),
    9,
  );
});

test('a blocked tile cuts a lane, and a blocked tile in the box is no intersection', () => {
  const cut = plusGraph(new Set([tileOf(18, 13)]));
  const east = cut.lanes.filter((l) => l.heading === HEADING_E);
  assert.deepEqual(
    east.map((l) => l.tiles.length).sort((a, b) => a - b),
    [5, 18],
  );
  const blockedBox = plusGraph(new Set([tileOf(12, 12)]));
  assert.equal(blockedBox.boxes.length, 0);
  // all nine tiles of the dead box are out of every lane
  for (const l of blockedBox.lanes) {
    for (const t of l.tiles) {
      const inBox =
        [11, 12, 13].includes(t % PLUS_COLS) && [11, 12, 13].includes(Math.floor(t / PLUS_COLS));
      assert.ok(!inBox, `lane tile ${t} lies in the dead box`);
    }
  }
});

// ── stops, claims, queues, turns ──────────────────────────────────────────────

test('a vehicle stops at the stop line before a crossing and before the box, dwells, then goes', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13)]);
  const v = byUid(w, 'e1');
  const at = { crossing: 0, box: 0 };
  let reachedBox = false;
  run(w, 60, 0.05, NO_PEOPLE, false, () => {
    if (reachedBox) return;
    const x = col(v);
    if (v.v < 0.25 && Math.abs(x - 6) < 0.06) at.crossing += 0.05;
    if (v.v < 0.25 && Math.abs(x - 10) < 0.06) at.box += 0.05;
    if (x > 10.6) reachedBox = true;
  });
  assert.ok(reachedBox, 'it must get through the crossing and reach the box');
  assert.ok(at.crossing >= C.TRAFFIC_STOP_DWELL_SEC, `stopped ${at.crossing}s at the crossing`);
  assert.ok(at.box >= C.TRAFFIC_STOP_DWELL_SEC, `stopped ${at.box}s at the box`);
  assert.ok(at.crossing <= 4 && at.box <= 4, 'and not for ever');
  assert.ok(w.counters.stops >= 2);
});

test('it enters a zone only after claiming ALL of it and the tile beyond, together', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13)]);
  const v = byUid(w, 'e1');
  let checked = false;
  run(w, 30, 0.05, NO_PEOPLE, false, () => {
    // the first moment its head is past the crossing's stop line
    if (!checked && col(v) > 6.15 && col(v) < 7) {
      for (const c of [7, 8, 9])
        assert.equal(w.holder(tileOf(c, 13)), v, `tile ${c} must already be held`);
      checked = true;
    }
  });
  assert.ok(checked);
});

test('vehicles that arrive together queue behind one another and never share a tile', () => {
  const g = plusGraph();
  // three on the east lane, close together, plus one on each cross lane: they all want the box
  const homes = [
    home(g, 'a', HEADING_E, 3, 13),
    home(g, 'b', HEADING_W, 20, 11),
    home(g, 'c', HEADING_S, 11, 3),
    home(g, 'd', HEADING_N, 13, 20),
  ];
  const w = world(g, homes);
  let shared = 0;
  run(w, 240, 0.05, NO_PEOPLE, false, () => {
    shared += auditClaims(w);
  });
  assert.equal(shared, 0, 'two vehicles held one tile');
  assert.ok(w.counters.tilesDriven > 200, `drove ${w.counters.tilesDriven}`);
  assert.ok(w.counters.stops >= 8);
  // a queue: more than one vehicle stopped in one lane
  const crowd = new TrafficWorld(g, [
    home(g, 'q1', HEADING_E, 3, 13),
    home(g, 'q2', HEADING_E, 5, 13, 'SEDAN_BLUE', 2),
  ]);
  crowd.setCap(2);
  run(crowd, 30);
  assert.ok(crowd.counters.maxQueue >= 2, `the longest queue was ${crowd.counters.maxQueue}`);
});

test('vehicles forced to contend for the box never hold a tile together, and the one who must wait does', () => {
  // an eastbound and a southbound vehicle sit at their stop lines for the SAME box tile (11,13); over many seeds
  // they contend in some, and in every one of them no tile is ever claimed twice
  let contended = 0;
  let bad = 0;
  for (let seed = 1; seed <= 40; seed++) {
    const g = plusGraph();
    const w = new TrafficWorld(
      g,
      [
        home(g, 'east', HEADING_E, 10, 13, 'TAXI', 2),
        home(g, 'south', HEADING_S, 11, 10, 'TAXI', 2),
      ],
      seed * 7919,
    );
    w.setCap(2);
    run(w, 40, 0.05, NO_PEOPLE, false, () => {
      bad += auditClaims(w);
    });
    if (w.counters.vehicleDenials > 0) contended++;
  }
  assert.equal(bad, 0, 'a tile was claimed twice');
  assert.ok(
    contended >= 5,
    `they contended in only ${contended} of 40 runs: the scenario does not test the rule`,
  );
});

test('at the box a vehicle goes straight, turns right or turns left, never back, and lands in the lane of its new heading', () => {
  const g = plusGraph();
  const homes = [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_W, 19, 11),
    home(g, 'c', HEADING_S, 11, 4),
    home(g, 'd', HEADING_N, 13, 19),
  ];
  const w = world(g, homes);
  const last = new Map<number, number>();
  const seen = new Set<string>();
  const bad: string[] = [];
  run(w, 900, 0.1, NO_PEOPLE, false, () => {
    for (const v of w.vehicles) {
      if (v.state === 'gone' || v.state === 'inactive') continue;
      const before = last.get(v.slot);
      if (before !== undefined && before !== v.heading && v.state !== 'in') {
        const delta = (v.heading - before + 4) & 3;
        seen.add(delta === 1 ? 'right' : delta === 3 ? 'left' : 'U-turn');
        // the head's tile must be a lane tile for its NEW heading
        const tile = v.route[Math.floor(v.head)];
        if (g.laneAt(tile, v.heading) < 0)
          bad.push(`tile ${tile} is not a lane for heading ${v.heading}`);
      }
      last.set(v.slot, v.heading);
    }
  });
  assert.ok(seen.has('right') && seen.has('left'), `turns seen: ${[...seen].join(', ')}`);
  assert.ok(!seen.has('U-turn'));
  assert.deepEqual(bad, []);
});

test('the claimed tiles, the route and the held body stay inside their bounds, however long it runs', () => {
  const g = plusGraph();
  const w = world(g, [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_N, 13, 19),
    home(g, 'c', HEADING_W, 19, 11),
  ]);
  let routeMax = 0;
  let heldMax = 0;
  run(w, 600, 0.1, NO_PEOPLE, false, () => {
    for (const v of w.vehicles) {
      routeMax = Math.max(routeMax, v.route.length);
      heldMax = Math.max(heldMax, w.heldTiles(v).length);
      assert.ok(v.body >= C.TRAFFIC_BODY_MIN_TILES && v.body <= C.TRAFFIC_BODY_MAX_TILES);
    }
  });
  assert.ok(routeMax <= C.TRAFFIC_ROUTE_MAX_TILES, `route ${routeMax}`);
  assert.ok(heldMax <= 16, `held ${heldMax}`);
  assert.ok(w.vehicles.length <= C.TRAFFIC_MAX_VEHICLES);
});

// ── priority ──────────────────────────────────────────────────────────────────

test('a pedestrian on the crossing holds the vehicle at the stop line, and it goes when they have gone', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13)]);
  const v = byUid(w, 'e1');
  const onCrossing = people({ walkerOwn: [tileOf(7, 13)] });
  let maxX = 0;
  run(w, 25, 0.05, onCrossing, false, () => (maxX = Math.max(maxX, col(v))));
  assert.ok(maxX <= 6.05, `it crept past the stop line to ${maxX}`);
  assert.ok(w.counters.pedestrianDenials > 0);
  run(w, 12, 0.05, NO_PEOPLE, false, () => (maxX = Math.max(maxX, col(v))));
  assert.ok(maxX > 8, 'it must go once the crossing is clear');
});

test('a pedestrian ABOUT to step onto the crossing also holds it (their next tiles count)', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13)]);
  const v = byUid(w, 'e1');
  let maxX = 0;
  run(
    w,
    20,
    0.05,
    people({
      walkerOwn: [tileOf(8, 9)],
      walkerNear: [tileOf(8, 11), tileOf(8, 12), tileOf(8, 13)],
    }),
    false,
    () => (maxX = Math.max(maxX, col(v))),
  );
  assert.ok(maxX <= 6.05);
});

test('a real agent beside the lane holds a vehicle too: nobody is ever driven past an agent within a tile', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13)]);
  const v = byUid(w, 'e1');
  // the agent stands on the centre line next to the crossing: adjacent to lane tiles 7, 8 and 9
  const agent = people({ agentOwn: [tileOf(8, 12)] });
  let maxX = 0;
  run(w, 25, 0.05, agent, false, () => (maxX = Math.max(maxX, col(v))));
  assert.ok(maxX <= 6.05, `a vehicle reached ${maxX} beside a real agent`);
});

test('nobody is driven through: a patrolling agent crossing the street is never inside a held tile', () => {
  const g = plusGraph();
  const homes = [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_W, 19, 11),
    home(g, 'c', HEADING_S, 11, 4),
    home(g, 'd', HEADING_N, 13, 19),
  ];
  const w = world(g, homes);
  let violations = 0;
  let t = 0;
  // an agent that paces across the east-west road at the crossing, back and forth, and along the cross street
  const path = [
    tileOf(8, 9),
    tileOf(8, 10),
    tileOf(8, 11),
    tileOf(8, 12),
    tileOf(8, 13),
    tileOf(8, 14),
    tileOf(8, 15),
    tileOf(8, 14),
    tileOf(8, 13),
    tileOf(8, 12),
    tileOf(8, 11),
    tileOf(8, 10),
  ];
  run(w, 300, 0.05, NO_PEOPLE, false, () => {
    t += 0.05;
    const here = path[Math.floor(t / 0.8) % path.length];
    const next = path[(Math.floor(t / 0.8) + 1) % path.length];
    // the world gives traffic the agent's own tile and the tile it is about to step onto
    w.update(0.0001, people({ agentOwn: [here], agentNear: [next] }), false);
    for (const v of w.vehicles) if (w.heldTiles(v).includes(here)) violations++;
  });
  assert.equal(violations, 0, 'a vehicle held the tile a real agent stood on');
  assert.ok(w.counters.tilesDriven > 50, 'and traffic still moved');
});

test('someone who steps INTO a vehicle makes it give way at once: it fades out and frees its tiles', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'e1', HEADING_E, 4, 13), home(g, 'f1', HEADING_E, 20, 13)]);
  run(w, 4);
  const v = byUid(w, 'e1');
  assert.equal(v.state, 'drive');
  const into = v.route[Math.floor(v.head)];
  const before = w.counters.yields;
  w.update(0.05, people({ agentOwn: [into] }), false);
  assert.equal(w.counters.yields, before + 1);
  assert.equal(v.state, 'out');
  for (const t of v.route)
    assert.notEqual(w.holder(t), v, 'its tiles must be free for others at once');
  run(w, 2);
  assert.ok(v.state === 'gone' || v.state === 'in' || v.state === 'drive');
});

test('an ambulance is an ordinary car: the same road, the same stops, the same everything as a taxi', () => {
  const g = plusGraph();
  const make = (kind: string): TrafficWorld =>
    world(g, [home(g, 'x', HEADING_E, 4, 13, kind), home(g, 'y', HEADING_W, 19, 11, kind)]);
  const taxi = make('TAXI');
  const emergency = make('AMBULANCE');
  const state = (w: TrafficWorld): string =>
    JSON.stringify(
      w.vehicles.map((v) => [v.slot, v.state, v.fade, v.head, v.claimed, v.v, v.route, v.heading]),
    );
  for (let k = 0; k < 2400; k++) {
    taxi.update(0.1, NO_PEOPLE, false);
    emergency.update(0.1, NO_PEOPLE, false);
    if (k % 300 === 0) assert.equal(state(emergency), state(taxi), `diverged at step ${k}`);
  }
  assert.equal(JSON.stringify(emergency.counters), JSON.stringify(taxi.counters));
  assert.ok(byUid(emergency, 'x').emergency);
  assert.ok(!byUid(taxi, 'x').emergency);
  // the three emergency LOOKS, and nothing else, carry the flag
  const flagged = [
    'TAXI',
    'SEDAN_BLUE',
    'HATCHBACK_RED',
    'SUV_SILVER',
    'POLICE_CRUISER',
    'FIRE_ENGINE',
    'AMBULANCE',
  ].filter((k) => make(k).vehicles[0].emergency);
  assert.deepEqual(flagged, ['POLICE_CRUISER', 'FIRE_ENGINE', 'AMBULANCE']);
});

// ── reduced motion, caps, determinism, restart ────────────────────────────────

test('reduced motion shows every vehicle where it is parked, holds it still, and never holds priority', () => {
  const g = plusGraph();
  const homes = [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_W, 19, 11),
    home(g, 'c', HEADING_S, 11, 4),
  ];
  const w = world(g, homes);
  w.update(0.1, NO_PEOPLE, true);
  const snapshot = () => JSON.stringify(w.vehicles.map((v) => [v.state, v.fade, v.head, v.route]));
  const still = snapshot();
  for (const v of w.vehicles) {
    assert.equal(v.state, 'drive');
    assert.equal(v.fade, 1);
    assert.equal(
      v.route[v.route.length - 1],
      g.lanes[v.home.lane].tiles[v.home.idx],
      'at its home tile, instantly',
    );
  }
  for (let k = 0; k < 200; k++) w.update(0.1, NO_PEOPLE, true);
  assert.equal(snapshot(), still, 'motion must be held');
  assert.equal(w.counters.tilesDriven, 0);
  // priority is not held: someone steps into one of them and it gives way even now
  const target = byUid(w, 'b');
  w.update(0.1, people({ agentOwn: [target.route[target.route.length - 1]] }), true);
  assert.equal(w.counters.yields, 1);
  // and motion resumes when the preference is off again
  for (let k = 0; k < 100; k++) w.update(0.1, NO_PEOPLE, false);
  assert.ok(w.counters.tilesDriven > 0);
});

test('under reduced motion a vehicle is not put down on top of a person', () => {
  const g = plusGraph();
  const w = world(g, [home(g, 'a', HEADING_E, 4, 13), home(g, 'b', HEADING_W, 19, 11)]);
  w.update(0.1, people({ walkerOwn: [tileOf(4, 13)] }), true);
  assert.equal(byUid(w, 'a').state, 'gone'); // its home tile has a person on it
  assert.equal(byUid(w, 'b').state, 'drive');
});

test('the cap decides how many drive, the rest are parked furniture again, and none leaves a claim behind', () => {
  const g = plusGraph();
  const homes = [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_W, 19, 11),
    home(g, 'c', HEADING_S, 11, 4),
    home(g, 'd', HEADING_N, 13, 19),
  ];
  const w = new TrafficWorld(g, homes);
  assert.equal(w.hiddenUids().size, 0);
  w.setCap(2);
  assert.equal(w.hiddenUids().size, 2);
  const v1 = w.hiddenVersion;
  w.setCap(2);
  assert.equal(w.hiddenVersion, v1, 'no change, no new version');
  run(w, 8);
  assert.equal(w.snapshot().active, 2);
  w.setCap(4);
  assert.equal(w.hiddenUids().size, 4);
  w.setCap(0);
  assert.equal(w.hiddenUids().size, 0);
  assert.equal(w.snapshot().active, 0);
  for (let t = 0; t < PLUS_COLS * PLUS_COLS; t++)
    assert.equal(w.holder(t), null, `tile ${t} is still claimed`);
  assert.equal(w.visible().length, 0);
  w.setCap(999);
  assert.equal(w.activeCap, 4, 'the cap is clamped to the slots that exist');
});

test('deterministic and restart-safe: the same world and seed give the same traffic, with the same ids, never duplicated', () => {
  const g = plusGraph();
  const homes = [
    home(g, 'a', HEADING_E, 4, 13),
    home(g, 'b', HEADING_W, 19, 11),
    home(g, 'c', HEADING_S, 11, 4),
    home(g, 'd', HEADING_N, 13, 19),
  ];
  const a = world(g, homes);
  const b = world(g, homes);
  run(a, 120, 0.1);
  run(b, 120, 0.1);
  const state = (w: TrafficWorld) =>
    JSON.stringify(w.vehicles.map((v) => [v.id, v.state, v.head, v.v, v.route, v.fade]));
  assert.equal(state(a), state(b));
  const ids = a.vehicles.map((v) => v.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual(ids, ['veh-000', 'veh-001', 'veh-002', 'veh-003']);
  // a "restart" is a new world: it starts from the same places and not from where the old one had got to
  const fresh = world(g, homes);
  assert.deepEqual(
    fresh.vehicles.map((v) => v.id),
    ids,
  );
  assert.equal(
    JSON.stringify(fresh.vehicles.map((v) => v.state)),
    JSON.stringify(['gone', 'gone', 'gone', 'gone']),
  );
  // a different seed gives a different (but equally lawful) street
  const other = new TrafficWorld(g, homes, 12345);
  other.setCap(4);
  run(other, 120, 0.1);
  assert.notEqual(state(other), state(a));
});

test('the sprite a vehicle shows is named by its kind and heading', () => {
  assert.equal(vehicleType('TAXI', HEADING_E), 'VEHICLE_TAXI_RIGHT');
  assert.equal(vehicleType('TAXI', HEADING_S), 'VEHICLE_TAXI_DOWN');
  assert.equal(vehicleType('POLICE_CRUISER', HEADING_W), 'VEHICLE_POLICE_CRUISER_LEFT');
  assert.equal(vehicleType('FIRE_ENGINE', HEADING_N), 'VEHICLE_FIRE_ENGINE_UP');
  assert.equal(KIND_ROAD, 0);
});
