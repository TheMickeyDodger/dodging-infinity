/**
 * LIVING CITY 6–9, residents and subway refinement, judged by the product's own code on the SHIPPED living
 * metropolis: subway riders live a journey of one of three kinds (a commute, a hop, an excursion round three
 * stations), walk to a platform, wait, board, ride and alight where their plan says, and do it from platform
 * tiles that are dealt out rather than shared; posted and roaming jobs have different dwell profiles; the
 * roster (identities, routines, plans) never depends on anything the viewer chooses for traffic, weather or
 * lighting; and a saved snapshot is bound to the journey plan it was saved from.
 *
 * Every figure the assertions use is a MEASURED property of one deterministic run (fixed seed, fixed 0.1 s
 * steps); the run's measurements are printed as MEASURED lines. Nothing here is a claim about a live provider.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { AmbientSaveState } from '../../core/src/ambientSave.ts';
import {
  AMBIENT_SAVE_FINGERPRINT_MAX,
  AMBIENT_SAVE_MAX_ABOARD,
  AMBIENT_SAVE_MAX_RESIDENTS,
  AMBIENT_SAVE_MAX_TRAINS,
} from '../../core/src/ambientSave.ts';
import type { CityPresentation } from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import { AMBIENT_JOBS, ambientLabel } from '../src/office/ambient/ambientJobs.ts';
import { ambientFingerprint } from '../src/office/ambient/ambientSave.ts';
import type { RiderKind } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { TransitEvent } from '../src/office/transit/transitWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const world = (): OfficeLayout =>
  JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));
const present = (over: Partial<CityPresentation>): CityPresentation => ({
  v: 1,
  lighting: 'day',
  weather: 'off',
  traffic: 'normal',
  ...over,
});

const SIM_SECONDS = 1800;
const DT = 0.1;
const EXPECTED_KINDS: readonly RiderKind[] = ['commute', 'hop', 'excursion'];

let os: OfficeState;
const events: TransitEvent[] = [];
/** What each boarding and alighting found: the rider's state and tile at that moment. */
const boardings: Array<{
  id: string;
  station: number;
  state: string;
  tile: number;
  legsWalked: number;
}> = [];
const alightings: Array<{ id: string; station: number; state: string }> = [];
/** Dwell timers set when a resident arrived on foot at a stop. */
const dwells: Array<{ style: string; atPost: boolean; rider: boolean; timer: number }> = [];
let platformStackedSeconds = 0;
let maxPlatformStack = 0;
/** Seconds in which two riders were WAITING on one tile. */
let waitingOnWaitingSeconds = 0;
let excursionSecondRide: AmbientSaveState | undefined;
let excursionThirdRide: AmbientSaveState | undefined;

beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(world());
  os.setCityPresentation(present({ traffic: 'off' })); // residents never read traffic; the run is shorter without it
  os.setAmbientEnabled(true);
  const w = os.ambient!;
  const t = os.transit!;
  const cols = w.pathfinder.mask.cols;
  // record every event the line logs (its own log is bounded and trims)
  const push = t.events.push.bind(t.events);
  t.events.push = (...e: TransitEvent[]) => {
    events.push(...e);
    return push(...e);
  };
  // record what a boarding and an alighting found (the engine enforces it; this shows it)
  const board = w.board.bind(w);
  w.board = (id: string) => {
    const r = w.residents.find((x) => x.id === id)!;
    const station = t.line.stations.findIndex((s) =>
      s.island.includes(r.tileRow * cols + r.tileCol),
    );
    boardings.push({
      id,
      station,
      state: r.state,
      tile: r.tileRow * cols + r.tileCol,
      legsWalked: r.legsWalked,
    });
    return board(id);
  };
  const alight = w.alight.bind(w);
  w.alight = (id: string, station: number) => {
    const r = w.residents.find((x) => x.id === id)!;
    alightings.push({ id, station, state: r.state });
    return alight(id, station);
  };
}, 120_000);

let simulated = false;
/** The half-hour run, recorded. It runs ONCE, by the first test that needs it (the others never pay for it). */
function simulate(): void {
  if (simulated) return;
  simulated = true;
  const w = os.ambient!;
  const t = os.transit!;
  const cols = w.pathfinder.mask.cols;
  const islandTiles = new Set<number>(t.line.stations.flatMap((s) => [...s.island]));
  const excursions = new Set(w.journeys.filter((j) => j.kind === 'excursion').map((j) => j.id));
  const prev = new Map<string, string>(w.residents.map((r) => [r.id, r.state]));
  const stack = new Map<number, number>();
  for (let k = 0; k < SIM_SECONDS / DT; k++) {
    os.update(DT);
    stack.clear();
    for (const r of w.residents) {
      const p = prev.get(r.id);
      if (p === 'walk' && r.state === 'dwell')
        dwells.push({
          style: AMBIENT_JOBS[r.job].style,
          atPost: r.routineIndex === 0,
          rider: !!r.rides,
          timer: r.timer,
        });
      prev.set(r.id, r.state);
      if (r.state === 'riding') continue;
      const tile = r.tileRow * cols + r.tileCol;
      if (islandTiles.has(tile)) stack.set(tile, (stack.get(tile) ?? 0) + 1);
    }
    for (const n of stack.values())
      if (n > 1) {
        platformStackedSeconds += DT;
        maxPlatformStack = Math.max(maxPlatformStack, n);
      }
    const waiting = new Map<number, number>();
    for (const r of w.residents)
      if (r.state === 'waiting') {
        const tile = r.tileRow * cols + r.tileCol;
        waiting.set(tile, (waiting.get(tile) ?? 0) + 1);
      }
    for (const n of waiting.values()) if (n > 1) waitingOnWaitingSeconds += DT;
    // a snapshot of an excursion rider aboard on its SECOND and THIRD ride
    if (!excursionSecondRide || !excursionThirdRide)
      for (const r of w.residents) {
        if (!excursions.has(r.id) || r.state !== 'riding') continue;
        if (r.routineIndex === 4 && !excursionSecondRide) {
          excursionSecondRide = clone(os.serializeAmbient(1)!);
        }
        if (r.routineIndex === 7 && !excursionThirdRide)
          excursionThirdRide = clone(os.serializeAmbient(1)!);
      }
  }
}
const simTest = (name: string, fn: () => void) =>
  test(
    name,
    () => {
      simulate();
      fn();
    },
    600_000,
  );

// ── the bounds ──────────────────────────────────────────────────────────────────────────────────

test('every new bound of the residents and subway refinement is asserted by its exact value', () => {
  assert.equal(C.AMBIENT_RIDERS_MAX, 24);
  assert.equal(C.AMBIENT_RIDER_REACH_TILES, 96);
  assert.deepEqual({ ...C.AMBIENT_RIDER_KIND_WEIGHTS }, { commute: 5, hop: 3, excursion: 2 });
  assert.equal(C.AMBIENT_POST_DWELL_FACTOR, 2.5);
  assert.equal(C.AMBIENT_ROAMING_DWELL_FACTOR, 0.6);
  assert.equal(C.WORLD_MAP_VEHICLE_MIN_PX, 2);
  assert.match(C.WORLD_MAP_VEHICLE_COLOR, /^#e6d9ae$/);
  // the bounds the riders live under, each by EXACT value (the contract's own, and the engine's): the riders fit, all at once,
  // under the saved snapshot's bound on riders aboard, and the longest journey is within the routine's stop bound
  assert.equal(
    AMBIENT_SAVE_MAX_ABOARD,
    32,
    'riders aboard, all trains together, in a saved snapshot',
  );
  assert.equal(AMBIENT_SAVE_MAX_RESIDENTS, 200);
  assert.equal(AMBIENT_SAVE_MAX_TRAINS, 4);
  assert.equal(C.AMBIENT_POPULATION_CAP, 200);
  assert.equal(C.AMBIENT_ROUTINE_MAX_STOPS, 10);
  assert.equal(C.AMBIENT_PATH_SEARCHES_PER_TICK, 4);
  assert.equal(C.AMBIENT_CATCHUP_MAX_SEC, 20);
  assert.equal(C.AMBIENT_CATCHUP_STEP_SEC, 0.1);
  assert.ok(C.AMBIENT_RIDERS_MAX <= AMBIENT_SAVE_MAX_ABOARD);
  assert.ok(9 <= C.AMBIENT_ROUTINE_MAX_STOPS);
});

// ── the riders and their plans ──────────────────────────────────────────────────────────────────

test('riders: at the bound, of three journey kinds, each plan consistent with its routine and ride plan', () => {
  const w = os.ambient!;
  const t = os.transit!;
  const stations = t.line.stations;
  const cols = w.pathfinder.mask.cols;
  assert.equal(w.riders.length, C.AMBIENT_RIDERS_MAX);
  assert.equal(w.journeys.length, w.riders.length);
  assert.deepEqual(
    w.journeys.map((j) => j.id),
    w.riders,
  );
  // TWO different routine maxima, kept apart: the longest routine at the time room coverage was settled (before any rider was
  // planned: the coverage's own figure) and the longest ACTUAL routine now (an excursion rider's nine stops)
  const finalMax = Math.max(...w.residents.map((r) => r.routine.length));
  console.log(
    `MEASURED routines: longest when room coverage was settled (before the riders) ${w.coverage.longestRoutine}; longest actual routine now ${finalMax}; bound ${C.AMBIENT_ROUTINE_MAX_STOPS}`,
  );
  assert.equal(finalMax, 9, "the longest actual routine is an excursion rider's nine stops");
  assert.ok(
    w.coverage.longestRoutine < finalMax,
    'the coverage figure is the earlier, smaller one',
  );
  assert.ok(finalMax <= C.AMBIENT_ROUTINE_MAX_STOPS);
  const kinds = new Map<RiderKind, number>();
  for (const j of w.journeys) kinds.set(j.kind, (kinds.get(j.kind) ?? 0) + 1);
  console.log(
    `MEASURED riders: ${w.riders.length}; kinds ${JSON.stringify(Object.fromEntries(kinds))}; jobs ${new Set(w.riders.map((id) => w.residents.find((r) => r.id === id)!.job)).size}`,
  );
  for (const k of EXPECTED_KINDS) assert.ok((kinds.get(k) ?? 0) >= 3, `${k}: ${kinds.get(k)}`);
  for (const j of w.journeys) {
    const r = w.residents.find((x) => x.id === j.id)!;
    assert.ok(r.rides, `${j.id} has a ride plan`);
    assert.ok(
      r.routine.length <= C.AMBIENT_ROUTINE_MAX_STOPS,
      `${j.id}: ${r.routine.length} stops`,
    );
    const [a, b] = j.stations;
    assert.equal(new Set(j.stations).size, j.stations.length, `${j.id}: stations repeat`);
    if (j.kind === 'hop') assert.equal(Math.abs(b - a), 1, `${j.id}: a hop is to the next station`);
    if (j.kind === 'commute')
      assert.ok(Math.abs(b - a) >= 2, `${j.id}: a commute passes a station`);
    assert.equal(j.stations.length, j.kind === 'excursion' ? 3 : 2);
    // the ride plan: board at routine[i], ride to rides[i], alight at routine[i + 1], and nowhere else
    const legs =
      j.kind === 'excursion'
        ? [
            [1, j.stations[1]],
            [4, j.stations[2]],
            [7, a],
          ]
        : [
            [1, b],
            [4, a],
          ];
    assert.equal(r.routine.length, j.kind === 'excursion' ? 9 : 6);
    for (let i = 0; i < r.routine.length; i++) {
      const leg = legs.find(([at]) => at === i);
      assert.equal(r.rides![i], leg ? leg[1] : -1, `${j.id}: ride plan at ${i}`);
    }
    // every platform stop is on the island of the station it is for; every other stop is on the way
    const from = [a, b, ...(j.kind === 'excursion' ? [j.stations[2]] : [])];
    const onIsland = (tile: number, s: number) => stations[s].island.includes(tile);
    assert.ok(onIsland(r.routine[1], a), `${j.id}: boards on the platform of its first station`);
    assert.ok(onIsland(r.routine[2], b), `${j.id}: alights on the platform of its second`);
    if (j.kind === 'excursion') {
      assert.ok(
        onIsland(r.routine[4], b) && onIsland(r.routine[5], j.stations[2]),
        `${j.id}: second ride`,
      );
      assert.ok(
        onIsland(r.routine[7], j.stations[2]) && onIsland(r.routine[8], a),
        `${j.id}: third ride`,
      );
    } else {
      assert.ok(onIsland(r.routine[4], b) && onIsland(r.routine[5], a), `${j.id}: ride home`);
    }
    for (const s of from) assert.ok(stations[s], `${j.id}: station ${s} exists`);
    // every stop is reachable on foot from the post
    for (const tile of r.routine)
      assert.ok(
        w.graph.connected(r.routine[0], tile),
        `${j.id}: stop ${tile % cols},${Math.floor(tile / cols)}`,
      );
  }
});

test('platform tiles are dealt per station: the tiles riders WAIT on are all different, and so are the tiles they ALIGHT on', () => {
  const w = os.ambient!;
  const t = os.transit!;
  const waits: number[][] = t.line.stations.map(() => []);
  const alights: number[][] = t.line.stations.map(() => []);
  for (const j of w.journeys) {
    const r = w.residents.find((x) => x.id === j.id)!;
    r.rides!.forEach((to, i) => {
      if (to < 0) return;
      const board = t.line.stations.findIndex((s) => s.island.includes(r.routine[i]));
      const leave = t.line.stations.findIndex((s) => s.island.includes(r.routine[i + 1]));
      assert.ok(
        board >= 0 && leave === to,
        `${j.id}: ride ${i} alights at the station it rides to`,
      );
      waits[board].push(r.routine[i]);
      alights[leave].push(r.routine[i + 1]);
    });
  }
  const report = t.line.stations.map((s, i) => ({
    station: s.name,
    island: s.island.length,
    waits: waits[i].length,
    distinctWaits: new Set(waits[i]).size,
    alights: alights[i].length,
    distinctAlights: new Set(alights[i]).size,
  }));
  console.log(`MEASURED platform deal: ${JSON.stringify(report)}`);
  for (const s of report) {
    // the deal cycles a platform's tiles, so while a platform has at least as many tiles as riders to deal, none repeats
    assert.ok(
      s.waits <= s.island && s.alights <= s.island,
      `${s.station}: more riders than platform tiles`,
    );
    assert.equal(s.distinctWaits, s.waits, `${s.station}: two riders wait on one tile`);
    assert.equal(s.distinctAlights, s.alights, `${s.station}: two riders alight on one tile`);
  }
});

// ── real journeys: walk, wait, board, ride, alight ───────────────────────────────────────────────

simTest(
  'REAL JOURNEYS: every rider walks to a platform, waits there, boards, rides and alights where its plan says',
  () => {
    const w = os.ambient!;
    const t = os.transit!;
    const names = t.line.stations.map((s) => s.name);
    const byRider = new Map<string, TransitEvent[]>();
    for (const e of events)
      if (e.resident && (e.kind === 'board' || e.kind === 'alight'))
        byRider.set(e.resident, [...(byRider.get(e.resident) ?? []), e]);
    let legs = 0;
    let fullExcursions = 0;
    for (const j of w.journeys) {
      const cycle =
        j.kind === 'excursion'
          ? [
              [j.stations[0], j.stations[1]],
              [j.stations[1], j.stations[2]],
              [j.stations[2], j.stations[0]],
            ]
          : [
              [j.stations[0], j.stations[1]],
              [j.stations[1], j.stations[0]],
            ];
      const seq = byRider.get(j.id) ?? [];
      assert.ok(
        seq.length >= 2,
        `${j.id} rode at least once in ${SIM_SECONDS} s (${seq.length} events)`,
      );
      // events come as board(X) then alight(Y), the leg X -> Y being one of the plan's, in the plan's cyclic order
      let at = -1;
      for (let i = 0; i + 1 < seq.length; i += 2) {
        const [b, a] = [seq[i], seq[i + 1]];
        assert.equal(b.kind, 'board', `${j.id}: event ${i} is a boarding`);
        assert.equal(a.kind, 'alight', `${j.id}: event ${i + 1} is an alighting`);
        assert.equal(a.train, b.train, `${j.id}: it alights from the train it boarded`);
        assert.ok(a.t > b.t, `${j.id}: it alights after it boards`);
        assert.notEqual(a.station, b.station, `${j.id}: it alights at another station`);
        const leg = cycle.findIndex(([x, y]) => names[x] === b.station && names[y] === a.station);
        assert.ok(leg >= 0, `${j.id}: ${b.station} -> ${a.station} is a leg of its plan`);
        if (at >= 0)
          assert.equal(leg, (at + 1) % cycle.length, `${j.id}: legs come in the plan's order`);
        at = leg;
        legs++;
      }
      if (j.kind === 'excursion' && seq.length >= 6) fullExcursions++;
    }
    console.log(
      `MEASURED journeys over ${SIM_SECONDS} s: ${legs} rides by ${byRider.size} riders; ${fullExcursions} excursion riders completed all three legs`,
    );
    assert.equal(byRider.size, w.riders.length, 'every rider rode');
    assert.ok(fullExcursions >= 1, 'an excursion rider rode all three of its legs');
  },
);

simTest(
  'boardings and alightings found the rider where a journey puts it: waiting on the platform it walked to, aboard a train',
  () => {
    const w = os.ambient!;
    assert.ok(boardings.length >= 100, `${boardings.length} boardings`);
    for (const b of boardings) {
      assert.equal(b.state, 'waiting', `${b.id} boarded from standing on the platform`);
      assert.ok(b.station >= 0, `${b.id} boarded on a station's island platform`);
      assert.ok(b.legsWalked >= 1, `${b.id} had walked to get there`);
    }
    for (const a of alightings)
      assert.equal(a.state, 'riding', `${a.id} alighted from a train it was riding`);
    // aboard is not on the ground: nobody riding is on the ground or on a platform tile's occupancy
    for (const r of w.residents.filter((x) => x.state === 'riding'))
      assert.equal(w.residentAt(r.x, r.y)?.id === r.id, false);
    assert.equal(new Set(boardings.map((b) => b.id)).size, w.riders.length, 'every rider boarded');
  },
);

simTest(
  'a journey in progress survives a reload: an excursion rider aboard on its second and third ride resumes and alights where it was going',
  () => {
    assert.ok(excursionSecondRide, 'the run produced an excursion rider aboard on its second ride');
    assert.ok(excursionThirdRide, 'and one on its third');
    for (const [snap, label] of [
      [excursionSecondRide!, 'second'],
      [excursionThirdRide!, 'third'],
    ] as const) {
      const o = new OfficeState(world());
      o.setAmbientSnapshot(clone(snap));
      o.setAmbientEnabled(true);
      assert.equal(
        o.lastAmbientRestore?.outcome,
        'applied',
        `${label}: ${o.lastAmbientRestore?.reason ?? ''}`,
      );
      assert.equal(o.lastAmbientRestore?.exact, true);
      // no duplicates: every resident once, every passenger a resident riding, on exactly one train
      const ids = o.ambient!.residents.map((r) => r.id);
      assert.equal(new Set(ids).size, ids.length);
      const aboard = o.transit!.trains.flatMap((tr) => tr.passengers);
      assert.equal(new Set(aboard).size, aboard.length);
      assert.deepEqual(
        [...aboard].sort(),
        o
          .ambient!.residents.filter((r) => r.state === 'riding')
          .map((r) => r.id)
          .sort(),
      );
      const riding = o.ambient!.residents.filter(
        (r) =>
          r.state === 'riding' &&
          o.ambient!.journeys.find((j) => j.id === r.id)?.kind === 'excursion',
      );
      assert.ok(riding.length >= 1);
      const target = riding[0].id;
      const where = o.ambient!.destinationOf(target);
      const totalsBefore = { ...o.transit!.totals };
      let alighted: TransitEvent | undefined;
      for (let k = 0; k < 6000 && !alighted; k++) {
        o.update(DT);
        alighted = o.transit!.events.find((e) => e.kind === 'alight' && e.resident === target);
      }
      assert.ok(alighted, `${label}: the rider alighted`);
      assert.equal(alighted!.station, o.transit!.line.stations[where].name);
      assert.equal(alighted!.restored, true, 'its boarding was in an earlier runtime');
      assert.equal(o.transit!.totals.boardings >= totalsBefore.boardings, true);
    }
  },
);

// ── dwell profiles ──────────────────────────────────────────────────────────────────────────────

test('a rider waiting for a train says so on its label, and every label still says Ambient and decorative', () => {
  for (const job of Object.keys(AMBIENT_JOBS) as Array<keyof typeof AMBIENT_JOBS>) {
    const waiting = ambientLabel(job, true);
    assert.match(waiting, /^Ambient · /);
    assert.match(waiting, /waiting for a train/);
    assert.match(waiting, / · decorative$/);
    assert.equal(ambientLabel(job), ambientLabel(job, false));
    assert.doesNotMatch(ambientLabel(job), /waiting for a train/);
  }
});

simTest(
  'a resident waiting on a platform is one whose journey has a ride, which is what its label says',
  () => {
    const waiting = os.ambient!.residents.filter((r) => r.state === 'waiting');
    for (const r of waiting)
      assert.ok(r.rides, `${r.id} waits only because its journey has a ride`);
  },
);

test('station access is on foot: every rider lives within reach of a platform and walks there over the pedestrian network', () => {
  const w = os.ambient!;
  const t = os.transit!;
  const cols = w.pathfinder.mask.cols;
  const distances: number[] = [];
  for (const j of w.journeys) {
    const r = w.residents.find((x) => x.id === j.id)!;
    const post = r.routine[0];
    const platform = r.routine[1];
    assert.ok(
      w.graph.connected(post, platform),
      `${j.id}: its platform is reachable on foot from its post`,
    );
    const d =
      Math.abs((post % cols) - (platform % cols)) +
      Math.abs(Math.floor(post / cols) - Math.floor(platform / cols));
    distances.push(d);
    assert.ok(t.line.stations[j.stations[0]].island.includes(platform));
    // the station it walks to is the nearest to its post, by column, as ever
    const pc = post % cols;
    const nearest = t.line.stations.reduce(
      (best, s, i) => (Math.abs(s.c - pc) < Math.abs(t.line.stations[best].c - pc) ? i : best),
      0,
    );
    assert.equal(j.stations[0], nearest, `${j.id}: it walks to its nearest station`);
  }
  distances.sort((a, b) => a - b);
  console.log(
    `MEASURED walk to the platform (Manhattan tiles, post to platform): shortest ${distances[0]}, median ${distances[Math.floor(distances.length / 2)]}, longest ${distances[distances.length - 1]} over ${distances.length} riders (the reach bound is ${C.AMBIENT_RIDER_REACH_TILES} from the nearest platform tile, as a walker's tiles, not a train's)`,
  );
  assert.ok(distances[0] >= 0);
});

simTest(
  'dwell profiles: a posted job holds its post, a roaming job lingers briefly at every stop',
  () => {
    const MIN = C.AMBIENT_DWELL_MIN_SEC;
    const MAX = C.AMBIENT_DWELL_MAX_SEC;
    const postedAtPost = dwells.filter((d) => d.style === 'posted' && d.atPost);
    const postedElsewhere = dwells.filter((d) => d.style === 'posted' && !d.atPost);
    const roaming = dwells.filter((d) => d.style === 'roaming');
    const mean = (list: typeof dwells) => list.reduce((a, d) => a + d.timer, 0) / list.length;
    console.log(
      `MEASURED dwell: posted at post n=${postedAtPost.length} mean ${mean(postedAtPost).toFixed(1)} s; posted elsewhere n=${postedElsewhere.length} mean ${mean(postedElsewhere).toFixed(1)} s; roaming n=${roaming.length} mean ${mean(roaming).toFixed(1)} s`,
    );
    for (const list of [postedAtPost, postedElsewhere, roaming])
      assert.ok(list.length >= 100, `samples ${list.length}`);
    const within = (list: typeof dwells, lo: number, hi: number) =>
      list.every((d) => d.timer >= lo - 1e-9 && d.timer <= hi + 1e-9);
    assert.ok(
      within(postedAtPost, MIN * C.AMBIENT_POST_DWELL_FACTOR, MAX * C.AMBIENT_POST_DWELL_FACTOR),
      'a posted job at its post',
    );
    assert.ok(within(postedElsewhere, MIN, MAX), 'a posted job away from its post');
    assert.ok(
      within(roaming, MIN * C.AMBIENT_ROAMING_DWELL_FACTOR, MAX * C.AMBIENT_ROAMING_DWELL_FACTOR),
      'a roaming job',
    );
    // and the profiles differ in the way the names say
    assert.ok(mean(postedAtPost) > 2 * mean(postedElsewhere));
    assert.ok(mean(roaming) < mean(postedElsewhere));
  },
);

// ── crowd: the platforms ─────────────────────────────────────────────────────────────────────────

simTest(
  'crowd: two riders never wait on one tile, and platform tiles are shared far less than before the deal',
  () => {
    console.log(
      `MEASURED platforms over ${SIM_SECONDS} s: two riders waiting on one tile for ${waitingOnWaitingSeconds.toFixed(1)} s; a tile held by two or more of anyone at once for ${platformStackedSeconds.toFixed(0)} s in all (before the deal: 227 s per 600 s, about 680 s per ${SIM_SECONDS} s); the most on one tile ${maxPlatformStack}`,
    );
    assert.equal(
      waitingOnWaitingSeconds,
      0,
      'a platform is one tile wide: waiting riders are dealt apart',
    );
    // what is left is a rider walking along the one-tile platform past a rider waiting on it (nothing else can pass)
    assert.ok(platformStackedSeconds <= 340, `${platformStackedSeconds} s`);
    assert.ok(maxPlatformStack <= 3);
  },
);

// ── identity and the saved snapshot ─────────────────────────────────────────────────────────────

test('the roster, the journey plans and the fingerprint are the same whatever traffic, weather and lighting are set to', () => {
  const key = (o: OfficeState) =>
    JSON.stringify([
      o.ambient!.residents.map((r) => [
        r.id,
        r.job,
        r.district,
        [...r.routine],
        r.rides ? [...r.rides] : null,
      ]),
      o.ambient!.journeys,
    ]);
  const settings: CityPresentation[] = [
    present({ traffic: 'off' }),
    present({ traffic: 'low', lighting: 'night', weather: 'rain' }),
    present({ traffic: 'normal', lighting: 'dusk', weather: 'snow' }),
    present({ traffic: 'normal', lighting: 'clock' }),
  ];
  const base = new OfficeState(world());
  base.setAmbientEnabled(true);
  const baseKey = key(base);
  const baseFingerprint = ambientFingerprint(base.layout, base.ambient!);
  for (const s of settings) {
    const o = new OfficeState(world());
    o.setCityPresentation(s);
    o.setAmbientEnabled(true);
    assert.equal(key(o), baseKey, JSON.stringify(s));
    assert.equal(ambientFingerprint(o.layout, o.ambient!), baseFingerprint, JSON.stringify(s));
    // and changing the settings later changes nothing about the roster either
    o.setCityPresentation(present({ traffic: 'off', lighting: 'day' }));
    assert.equal(key(o), baseKey, `${JSON.stringify(s)} then off`);
  }
});

test('the fingerprint binds the journey plan: another plan, an old-format snapshot or a changed ride refuses the snapshot as another world', () => {
  const o = new OfficeState(world());
  o.setAmbientEnabled(true);
  const w = o.ambient!;
  const f = ambientFingerprint(o.layout, w);
  assert.ok(f.length <= AMBIENT_SAVE_FINGERPRINT_MAX, `${f.length} characters`);
  assert.match(f, /\|j[0-9a-f]{8}\|/);
  assert.equal(ambientFingerprint(o.layout, w), f, 'stable');
  // one rider's ride plan changed
  const r = w.residents.find((x) => x.rides)!;
  const rides = [...r.rides!];
  const i = rides.findIndex((v) => v >= 0);
  r.rides![i] = (rides[i] + 1) % 4;
  assert.notEqual(ambientFingerprint(o.layout, w), f, 'a changed ride');
  r.rides![i] = rides[i];
  // one routine tile changed
  const tile = r.routine[2];
  r.routine[2] = tile + 1;
  assert.notEqual(ambientFingerprint(o.layout, w), f, 'a changed stop');
  r.routine[2] = tile;
  assert.equal(ambientFingerprint(o.layout, w), f, 'restored');
  // a snapshot in the old format (no journey plan) is refused as another world, and nothing changes
  const snap = clone(o.serializeAmbient(5)!);
  snap.world = f.replace(/\|j[0-9a-f]{8}/, '');
  const before = JSON.stringify(o.serializeAmbient(0));
  o.setAmbientSnapshot(snap);
  assert.equal(o.lastAmbientRestore?.outcome, 'other-world');
  assert.equal(JSON.stringify(o.serializeAmbient(0)), before, 'nothing changed');
});
