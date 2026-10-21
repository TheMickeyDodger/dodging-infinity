/**
 * WORKING-OFFICE VISIBLE OCCUPANCY (off-duty rotation).
 *
 * The policy, pinned on the SHIPPED worlds through OfficeState exactly as the product builds them:
 * - SCOPE: a pool that holds a seat is a working office. There are three kinds, each tested on its own:
 *   the DI default world's Mission-zone pool (the fresh office), the legacy single `Office` fallback
 *   pool (expanded-office), and the Palace pool (living-metropolis). Every other district, and every
 *   rider, is untouched.
 * - BOUND (density-aware, per pool): at most min(AMBIENT_WORKING_OFFICE_ON_DUTY,
 *   max(1, ceil(T / AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT))) residents of such a pool are
 *   on duty (drawn, walking, dwelling, hoverable) at any instant; the rest are OFF DUTY. So a small room
 *   shows fewer than a large office, and a pool already at or under its bound is untouched. The chosen
 *   values, and the exact count in each required case, are pinned EXACTLY.
 * - OFF DUTY IS NOT DELETION: it is derived (never saved) and frozen. Every id, job, routine, ride
 *   plan, count and the saved-state fingerprint are exactly what they were; an off-duty resident keeps
 *   its tile, state, timer and pending stop until its turn, is not drawn, not hoverable and not an
 *   obstacle; hand-over happens only at a dwell boundary, so nobody vanishes mid-stride; the office
 *   stays alive by rotating through the roster.
 * - SAVES: a snapshot saved at the OLD density (every resident on the floor) still loads, applied and
 *   exact, and shows at most the bound on duty on reopen.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { afterEach, beforeAll, beforeEach, test, vi } from 'vitest';

import { ambientLegendText } from '../src/components/ambientLegendText.ts';
import {
  AMBIENT_DUTY_ROTATION_SEC,
  AMBIENT_WORKING_OFFICE_ON_DUTY,
  AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT,
  DI_ZONE_COLORS,
} from '../src/constants.ts';
import { snapshotAmbient } from '../src/office/ambient/ambientProbe.ts';
import { serializeAmbient } from '../src/office/ambient/ambientSave.ts';
import type { AmbientResident } from '../src/office/ambient/ambientWorld.ts';
import { AmbientWorld, buildAmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { buildDiWorld, cropToContent } from '../src/office/layout/diWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const world = (name: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds', `${name}.json`), 'utf-8'));
const defaultRoom = (): OfficeLayout =>
  cropToContent(
    JSON.parse(
      fs.readFileSync(path.join(ROOT, 'webview-ui/public/assets/default-layout-1.json'), 'utf-8'),
    ),
  );

const STEP = 0.1;
// The chosen values, stated here independently of the code.
const ON_DUTY_CAP = 16;
const TILES_PER_RESIDENT = 34;
const ROTATION_SEC = 6;
/** The bound the policy gives a pool: the cap, or fewer in a small floor (never below one). */
const boundFor = (tiles: number): number =>
  Math.min(ON_DUTY_CAP, Math.max(1, Math.ceil(tiles / TILES_PER_RESIDENT)));

beforeAll(() => loadBundledCatalog());
// Seat choice uses Math.random: a seeded stream keeps every run on the same seats.
beforeEach(() => {
  let s = 0x2f6b1d3;
  vi.spyOn(Math, 'random').mockImplementation(() => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  });
});
afterEach(() => vi.restoreAllMocks());

/** `tiles` is T = `pool.tiles.length`, the pool's resident-eligible tiles as `addPool` builds them (walkable, on the main network, not a seat tile or a seat tile's walkable neighbour, in that place): the formula's input, NOT the room's or the place's area. `total` and `onDuty` are the pool's residents and the resulting count on duty. */
type Kind = {
  name: string;
  label: string;
  tiles: number;
  total: number;
  onDuty: number;
  make: () => OfficeState;
};

/** The DI default world exactly as the app composes it: cropped room, one band, the room's zone is the first Mission. */
function freshOffice(agents: number): OfficeState {
  const built = buildDiWorld(defaultRoom(), 1, { room: 'M', slots: [] }, () => 'Mission');
  const o = new OfficeState(built.layout);
  o.setAreaMappings(built.mappings);
  o.setAmbientEnabled(true);
  for (let id = 1; id <= agents; id++) o.addAgent(id, undefined, undefined, undefined, true, 'M');
  return o;
}
function named(layoutName: string, agents: number): OfficeState {
  const o = new OfficeState(world(layoutName));
  o.setAmbientEnabled(true);
  for (let id = 1; id <= agents; id++) o.addAgent(id, undefined, undefined, undefined, true);
  return o;
}
const KINDS: Kind[] = [
  {
    name: 'default fresh office (DI Mission-zone pool)',
    label: 'Mission',
    tiles: 105,
    total: 140,
    onDuty: 4,
    make: () => freshOffice(4),
  },
  {
    name: 'expanded-office (legacy single Office pool)',
    label: 'Office',
    tiles: 718,
    total: 140,
    onDuty: 16,
    make: () => named('expanded-office', 8),
  },
  {
    name: 'living-metropolis (Palace pool)',
    label: 'Palace',
    tiles: 2183,
    total: 11,
    onDuty: 11,
    make: () => named('living-metropolis', 16),
  },
];

function run(o: OfficeState, seconds: number, every = 0, probe?: () => void): void {
  for (let k = 1; k <= Math.round(seconds / STEP); k++) {
    o.update(STEP);
    if (probe && every > 0 && k % Math.round(every / STEP) === 0) probe();
  }
}
const poolSize = (o: OfficeState, label: string): number =>
  o.ambient!.residents.filter((r) => r.district === label).length;
const onDuty = (o: OfficeState, label: string): number =>
  o.ambient!.visibleResidents().filter((r) => r.district === label).length;
const roster = (w: AmbientWorld) =>
  w.residents.map((r) => [r.id, r.job, r.district, [...r.routine], r.rides ? [...r.rides] : null]);

test('the chosen bounds are pinned to their exact values', () => {
  assert.equal(AMBIENT_WORKING_OFFICE_ON_DUTY, ON_DUTY_CAP);
  assert.equal(AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT, TILES_PER_RESIDENT);
  assert.equal(AMBIENT_DUTY_ROTATION_SEC, ROTATION_SEC);
});

for (const kind of KINDS) {
  test(`${kind.name}: the pool has the pinned resident-eligible tiles (T = ${kind.tiles}) and residents, and the policy gives exactly ${kind.onDuty} on duty`, () => {
    const o = kind.make();
    const roster = o.ambient!.dutyRosters().find((g) => g.label === kind.label);
    assert.ok(roster, `${kind.label} is a working office`);
    assert.equal(
      roster.tiles,
      kind.tiles,
      `${kind.label}: resident-eligible tiles T (pool.tiles.length)`,
    );
    assert.equal(roster.members, kind.total, `${kind.label}: residents of the pool`);
    assert.equal(roster.cap, boundFor(kind.tiles), 'the bound is the stated formula');
    assert.equal(Math.min(roster.cap, roster.members), kind.onDuty, 'and the count it gives');
    assert.equal(onDuty(o, kind.label), kind.onDuty, `${kind.label}: on duty at t = 0`);
  });
}

test('density: a small floor shows fewer on duty than a large office, never fewer than one, and a pool under its bound is untouched', () => {
  const o = named('expanded-office', 8);
  const w = o.ambient!;
  const members = w.residents.slice(0, 40);
  const cases: Array<[string, number, number, number]> = [
    // label, T (resident-eligible tiles), members offered, expected on duty
    ['default-office-sized', 105, 40, 4], // ceil(105 / 34) = 4
    ['expanded-sized', 718, 40, 16], // ceil(718 / 34) = 22, capped to 16
    ['tiny', 3, 40, 1], // never fewer than one
    ['roomy', 5000, 40, 16], // the cap binds
    ['palace-sized', 2183, 11, 11], // ceil(2183 / 34) = 65, capped to 16: 11 residents are under it, untouched
    ['sparse', 105, 3, 3], // 3 residents are under the bound of 4: untouched
  ];
  for (const [label, tiles, offered, want] of cases) {
    w.configureDuty(
      [{ label, tiles, members: members.slice(0, offered) }],
      ON_DUTY_CAP,
      TILES_PER_RESIDENT,
    );
    assert.equal(
      members.slice(0, offered).filter((r) => w.isOnDuty(r)).length,
      want,
      `${label}: T = ${tiles} resident-eligible tiles, ${offered} residents`,
    );
  }
  // every group is listed (also an untouched one) so the evidence can name the tiles it used
  w.configureDuty(
    [{ label: 'sparse', tiles: 105, members: members.slice(0, 3) }],
    ON_DUTY_CAP,
    TILES_PER_RESIDENT,
  );
  assert.deepEqual(w.dutyRosters(), [{ label: 'sparse', tiles: 105, members: 3, cap: 4 }]);
});

for (const kind of KINDS) {
  test(`${kind.name}: at most the bound is on duty at every instant, and the office stays populated`, () => {
    const o = kind.make();
    const size = poolSize(o, kind.label);
    assert.ok(size > 0, `precondition: the ${kind.label} pool has residents`);
    const want = kind.onDuty;
    const seen: number[] = [];
    seen.push(onDuty(o, kind.label));
    run(o, 300, 1, () => seen.push(onDuty(o, kind.label)));
    assert.deepEqual(
      seen.filter((n) => n !== want),
      [],
      `${kind.label}: on duty every second for 300 s (want ${want} of ${size}): ${[...new Set(seen)].join(',')}`,
    );
  });

  test(`${kind.name}: ids, jobs, routines, rides, count and the save fingerprint are untouched`, () => {
    const o = kind.make();
    const w = o.ambient!;
    const fingerprint = o.serializeAmbient(0)!.world;
    const before = roster(w);
    const total = w.residents.length;
    run(o, 200);
    assert.deepEqual(roster(w), before, 'the roster is exactly what it was');
    assert.equal(w.residents.length, total);
    assert.equal(
      o.serializeAmbient(0)!.world,
      fingerprint,
      'the fingerprint a snapshot binds to did not move',
    );
    assert.equal(
      o.serializeAmbient(0)!.residents.length,
      total,
      'a save holds every resident, on duty or not',
    );
  });
}

test('the legend can say how many exist and how many are on duty: summary() carries both', () => {
  const o = named('expanded-office', 8);
  const s = o.ambient!.summary();
  assert.equal(s.total, o.ambient!.residents.length, 'total is the whole roster');
  assert.equal(s.onDuty, ON_DUTY_CAP);
  assert.equal(s.offDuty, s.total - ON_DUTY_CAP);
  assert.equal(s.onDuty + s.offDuty, s.total);
  assert.equal(
    s.byJob.reduce((n, j) => n + j.count, 0),
    s.total,
    'the job counts still add up to the whole roster',
  );
});

test('the legend never implies fewer residents exist than do, and says so when some are off duty', () => {
  assert.equal(
    ambientLegendText({ total: 140, onDuty: 140, offDuty: 0 }),
    'Ambient residents · 140 · decorative',
  );
  assert.equal(
    ambientLegendText({ total: 140, onDuty: 16, offDuty: 124 }),
    'Ambient residents · 16 on duty of 140 · decorative',
  );
  const o = named('expanded-office', 8);
  assert.equal(
    ambientLegendText(o.ambient!.summary()),
    `Ambient residents · ${ON_DUTY_CAP} on duty of ${o.ambient!.residents.length} · decorative`,
  );
});

test('the Palace is a working office under the same rule, holds fewer residents than its bound, so it is UNCHANGED, and every OTHER district stays exactly as lively', () => {
  const o = named('living-metropolis', 16);
  const w = o.ambient!;
  const palace = w.residents.filter((r) => r.district === 'Palace');
  const palaceOn = palace.filter((r) => w.isOnDuty(r)).length;
  // the same rule as every other working office. The Palace holds 11 residents with T = 2183 resident-eligible tiles (bound 16), below
  // its bound, so NOTHING about it changes: this is a consequence of the pinned values, not a guarantee.
  assert.equal(palace.length, 11);
  assert.equal(palaceOn, 11, 'every Palace resident is on duty: unchanged');
  assert.equal(palace.length - palaceOn, 0, 'none is off duty');
  // every other district, and every rider, is untouched: all on duty, now and while the world runs
  const others = (): number =>
    w.residents.filter((r) => r.district !== 'Palace' && !w.isOnDuty(r)).length;
  assert.equal(others(), 0);
  run(o, 120);
  assert.equal(others(), 0, 'no resident of another district ever steps off duty');
  assert.equal(w.visibleResidents().length, w.residents.length - (palace.length - palaceOn));
  for (const id of w.riders)
    assert.equal(
      w.isOnDuty(w.residents.find((r) => r.id === id)!),
      true,
      'a rider is never rotated',
    );
  // nothing about identity, job, routine or ride plan moved
  assert.equal(w.residents.length, 145);
  assert.equal(w.summary().total, 145, 'the legend total is the whole simulated roster');
});

// -- scope, on a small districted floor: A holds a seat, B does not ------------------------------

function floor(
  cols: number,
  rows: number,
): { tileMap: TileType[][]; areaTiles: Array<string | null> } {
  const tileMap = Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) =>
      r === 0 || r === rows - 1 || c === 0 || c === cols - 1 ? TileType.WALL : TileType.FLOOR_1,
    ),
  );
  const areaTiles: Array<string | null> = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++)
      areaTiles.push(tileMap[r][c] === TileType.WALL ? null : c < cols / 2 ? 'A' : 'B');
  return { tileMap, areaTiles };
}
const districted = (opts: {
  seatsIn: 'A' | 'B' | null;
  cap: number;
  population: number;
}): AmbientWorld => {
  const { tileMap, areaTiles } = floor(32, 10);
  return buildAmbientWorld({
    layout: {
      cols: 32,
      rows: 10,
      areas: [
        { label: 'A', color: DI_ZONE_COLORS[0] },
        { label: 'B', color: DI_ZONE_COLORS[1] },
      ],
      areaTiles,
    },
    tileMap,
    blockedTiles: new Set(),
    homeRegions: null,
    seats:
      opts.seatsIn === null
        ? []
        : [{ uid: 's1', seatCol: opts.seatsIn === 'A' ? 4 : 24, seatRow: 4 }],
    population: opts.population,
    onDutyCap: opts.cap,
  });
};
const visibleIn = (w: AmbientWorld, label: string): number =>
  w.visibleResidents().filter((r) => r.district === label).length;
const sizeIn = (w: AmbientWorld, label: string): number =>
  w.residents.filter((r) => r.district === label).length;

test('scope: only a pool that holds a seat rotates; a seatless district stays fully on duty', () => {
  const w = districted({ seatsIn: 'A', cap: 4, population: 40 });
  assert.ok(
    sizeIn(w, 'A') > 4 && sizeIn(w, 'B') > 4,
    'precondition: both districts are larger than the bound',
  );
  assert.equal(visibleIn(w, 'A'), 4, 'the working office (it holds the seat) is bounded');
  assert.equal(visibleIn(w, 'B'), sizeIn(w, 'B'), 'the other district is untouched');
  for (let i = 0; i < 3000; i++) w.update(STEP);
  assert.equal(visibleIn(w, 'A'), 4);
  assert.equal(visibleIn(w, 'B'), sizeIn(w, 'B'));
});

test('scope: with no seat anywhere nothing rotates (there is no working office)', () => {
  const w = districted({ seatsIn: null, cap: 4, population: 40 });
  assert.equal(w.visibleResidents().length, w.residents.length);
  assert.equal(w.visibleResidents(), w.residents);
});

test('scope: the legacy Office fallback pool rotates when it holds a seat, and a pool no larger than the bound does not', () => {
  const { tileMap } = floor(32, 10);
  const build = (cap: number, seats: boolean) =>
    buildAmbientWorld({
      layout: { cols: 32, rows: 10 },
      tileMap,
      blockedTiles: new Set(),
      homeRegions: null,
      seats: seats ? [{ uid: 's1', seatCol: 4, seatRow: 4 }] : [],
      population: 30,
      onDutyCap: cap,
      onDutyTilesPerResident: 0, // this test is about the cap and the scope; density has its own test
    });
  const w = build(5, true);
  assert.equal(
    new Set(w.residents.map((r) => r.district)).size,
    1,
    'precondition: one Office pool',
  );
  assert.equal(w.visibleResidents().length, 5);
  assert.equal(
    build(30, true).visibleResidents().length,
    30,
    'a pool of exactly the bound is all on duty',
  );
  assert.equal(build(5, false).visibleResidents().length, 30, 'no seat: no working office');
});

// -- the rotation itself, on expanded-office (140 residents on one working floor) -----------------

test('hand-over happens only at a dwell boundary; off duty is frozen; the same resident returns where it left', () => {
  const o = named('expanded-office', 8);
  const w = o.ambient!;
  const frozen = new Map<string, string>();
  const key = (r: AmbientResident): string =>
    `${r.tileCol},${r.tileRow},${r.state},${r.timer},${r.routineIndex},${r.route.length}`;
  for (const r of w.residents) if (!w.isOnDuty(r)) frozen.set(r.id, key(r)); // off duty from t = 0
  let prevVisible = new Set(w.visibleResidents().map((r) => r.id));
  let prevState = new Map(w.residents.map((r) => [r.id, `${r.state}|${r.moveProgress}`]));
  let left = 0;
  let returned = 0;
  for (let k = 0; k < 4000; k++) {
    o.update(STEP);
    const now = new Set(w.visibleResidents().map((r) => r.id));
    for (const id of prevVisible)
      if (!now.has(id)) {
        left++;
        assert.equal(
          prevState.get(id),
          'dwell|0',
          `${id} left while ${prevState.get(id)}: only a standing resident may leave`,
        );
        frozen.set(id, key(w.residents.find((r) => r.id === id)!));
      }
    for (const id of now)
      if (!prevVisible.has(id)) {
        returned++;
        const r = w.residents.find((x) => x.id === id)!;
        const [col, row] = frozen.get(id)!.split(',');
        assert.deepEqual(
          [`${r.tileCol}`, `${r.tileRow}`],
          [col, row],
          `${id} returns on the tile it left`,
        );
        frozen.delete(id);
      }
    for (const [id, was] of frozen)
      assert.equal(
        key(w.residents.find((r) => r.id === id)!),
        was,
        `${id} is frozen while off duty`,
      );
    prevVisible = now;
    prevState = new Map(w.residents.map((r) => [r.id, `${r.state}|${r.moveProgress}`]));
  }
  assert.ok(
    left >= 20 && returned >= 20,
    `the office rotated: ${left} left, ${returned} returned in 400 s`,
  );
});

test('rotation keeps the office alive: every resident takes its turn, one hand-over per interval', () => {
  const o = named('expanded-office', 8);
  const w = o.ambient!;
  const seen = new Set<string>();
  let changes = 0;
  let last = w
    .visibleResidents()
    .map((r) => r.id)
    .join('|');
  const cycle = w.residents.length * ROTATION_SEC;
  for (let k = 0; k < Math.round((cycle + 120) / STEP); k++) {
    o.update(STEP);
    for (const r of w.visibleResidents()) seen.add(r.id);
    const now = w
      .visibleResidents()
      .map((r) => r.id)
      .join('|');
    if (now !== last) changes++;
    last = now;
  }
  assert.equal(
    seen.size,
    w.residents.length,
    'every one of the 140 residents was on duty at some time',
  );
  assert.ok(changes >= w.residents.length, `${changes} hand-overs in ${cycle + 120} s`);
});

test('an off-duty resident can never be hovered or clicked, and the drawn list never holds one', () => {
  const o = named('expanded-office', 8);
  const w = o.ambient!;
  run(o, 30);
  const off = w.residents.filter((r) => !w.isOnDuty(r));
  assert.ok(off.length > 0);
  for (const r of off.slice(0, 40)) {
    assert.notEqual(w.residentAt(r.x, r.y - 8)?.id, r.id, `${r.id} is not hit-testable`);
    assert.notEqual(
      o.getAmbientAt(r.x, r.y - 8)?.id,
      r.id,
      `${r.id} is not hoverable through OfficeState`,
    );
  }
  const drawn = o.getAmbientResidents();
  assert.equal(drawn.length, w.visibleResidents().length, 'the draw list is the visible view');
  for (const r of drawn) assert.equal(w.isOnDuty(r), true);
  // an on-duty resident is still found
  const on = drawn.find((r) => r.state !== 'riding')!;
  assert.equal(o.getAmbientAt(on.x, on.y - 8)?.id !== undefined, true);
});

test('an off-duty resident is not an obstacle, in the pedestrians or in the traffic give-way', () => {
  // A one-tile corridor: the walker would queue behind a standing resident that counted.
  const cols = 40;
  const tileMap = Array.from({ length: 3 }, (_, r) =>
    Array.from({ length: cols }, () => (r === 1 ? TileType.FLOOR_1 : TileType.WALL)),
  );
  const w0 = buildAmbientWorld({
    layout: { cols, rows: 3 },
    tileMap,
    blockedTiles: new Set(),
    homeRegions: null,
    seats: [{ uid: 's', seatCol: 0, seatRow: 1 }],
    population: 2,
    onDutyCap: 1,
  });
  assert.equal(w0.residents.length, 2);
  const [a, b] = w0.residents;
  const place = (r: AmbientResident, c: number): void => {
    r.tileCol = c;
    r.tileRow = 1;
    r.x = c * 16 + 8;
    r.y = 24;
  };
  place(a, 5);
  place(b, 20); // right on the walker's way
  const fresh = new AmbientWorld(
    w0.residents,
    w0.pathfinder,
    w0.graph,
    w0.seed,
    w0.seatZone,
    new Map(),
    w0.placeOf,
  );
  fresh.configureDuty([{ label: 'Office', tiles: 1000, members: w0.residents }], 1, 0);
  const walker = fresh.residents.find((r) => fresh.isOnDuty(r))!;
  const parked = fresh.residents.find((r) => !fresh.isOnDuty(r))!;
  place(walker, 5);
  place(parked, 20);
  walker.route = Int32Array.from({ length: 30 }, (_, k) => cols + 6 + k);
  walker.routePos = 0;
  walker.moveProgress = 0;
  walker.state = 'walk';
  for (let i = 0; i < 2000 && walker.state === 'walk'; i++) fresh.update(0.05);
  assert.equal(fresh.stats.congestionWaits, 0, 'it never queued behind the invisible resident');
  assert.equal(walker.tileCol, 35, 'and arrived');
  assert.equal(parked.tileCol, 20, 'the parked one never moved');
});

test('reduced motion freezes the rotation: the same residents stay on duty', () => {
  const o = named('expanded-office', 8);
  o.reducedMotion = true;
  const before = o
    .ambient!.visibleResidents()
    .map((r) => r.id)
    .join('|');
  run(o, 120);
  assert.equal(
    o
      .ambient!.visibleResidents()
      .map((r) => r.id)
      .join('|'),
    before,
  );
});

// -- saved worlds: the OLD density (everyone on the floor) still loads --------------------------------

for (const [name, make, onDutyWanted] of [
  ['default fresh office', () => freshOffice(4), 4],
  ['expanded-office', () => named('expanded-office', 8), 16],
] as const) {
  test(`${name}: a snapshot saved with EVERY resident on the floor loads applied and exact, and shows only the bound on reopen`, () => {
    const o1 = make();
    const w1 = o1.ambient!;
    // The OLD density: the same roster built with no bound, run, then saved.
    const old = buildAmbientWorld({
      layout: o1.layout,
      tileMap: o1.tileMap,
      blockedTiles: o1.blockedTiles,
      homeRegions: null,
      seats: [...o1.seats.values()],
      onDutyCap: 1_000_000,
      onDutyTilesPerResident: 0,
    });
    assert.equal(
      old.visibleResidents().length,
      old.residents.length,
      'precondition: the old world has everyone on the floor',
    );
    for (let k = 0; k < 1500; k++) old.update(STEP);
    const snapshot = JSON.parse(JSON.stringify(serializeAmbient(o1.layout, old, null, 1)));
    assert.equal(
      snapshot.world,
      o1.serializeAmbient(0)!.world,
      'the bound does not move the fingerprint',
    );

    const o2 = make();
    o2.setAmbientSnapshot(snapshot);
    const result = o2.lastAmbientRestore!;
    assert.equal(result.outcome, 'applied', result.reason ?? 'restored without a stated reason');
    assert.equal(result.exact, true, 'applied, exact');
    assert.equal(result.residentsRestored, snapshot.residents.length);
    const w2 = o2.ambient!;
    assert.deepEqual(
      w2.residents.map((r) => r.id),
      w1.residents.map((r) => r.id),
      'the same residents',
    );
    assert.deepEqual(roster(w2), roster(w1), 'with the same jobs and routines');
    assert.equal(
      w2.visibleResidents().length,
      onDutyWanted,
      'and only the bound is on duty on reopen',
    );
    // every saved tile is kept, on duty or not
    const saved = new Map<string, number[]>(
      snapshot.residents.map((e: [string, number, number, number]) => [e[0], [e[2], e[3]]]),
    );
    for (const r of w2.residents)
      assert.deepEqual([r.tileCol, r.tileRow], saved.get(r.id), `${r.id} is where it was saved`);
    run(o2, 120);
    assert.equal(w2.visibleResidents().length, onDutyWanted);
    assert.equal(
      o2.serializeAmbient(0)!.world,
      snapshot.world,
      'still the world the snapshot was saved from',
    );
  });
}

test('the probe keeps the FULL simulated roster and flags who is on duty', () => {
  const o = named('expanded-office', 8);
  const entries = snapshotAmbient(o);
  assert.equal(entries.length, o.ambient!.residents.length, 'every simulated resident');
  assert.equal(entries.filter((e) => e.onDuty).length, ON_DUTY_CAP);
  const on = new Set(o.getAmbientResidents().map((r) => r.id));
  for (const e of entries) assert.equal(e.onDuty, on.has(e.id));
});
