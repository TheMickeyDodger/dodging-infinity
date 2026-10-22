/**
 * THE THREE GRAND OFFICES: detection (office/layout/diOffices.ts) against the REAL shipped worlds. Proves: living-metropolis has exactly three offices with
 * the seat / workstation numbers the design measured; every satellite seat is desk-facing and inside its own office district; the home keeps its lounge
 * chairs as eligible-but-not-workstations; the receptionist's reserved seat is in none of them; expanded-office, vast-forest, metropolis and the bundled
 * default are NOT detected and keep their legacy seating exactly; the label, desk-facing, threshold and reachability rules each exclude what they must;
 * Generic Pixel (the DI office switch off) is unchanged; and detection runs once per layout, inside a time bound.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import {
  AUTO_ON_FACING_DEPTH,
  DI_HOME_OFFICE_LABEL,
  DI_OFFICE_DISTRICT_PATTERNS,
  DI_OFFICE_MAX_OFFICES,
  DI_OFFICE_MIN_WORKSTATIONS,
  DI_RECEPTION_BENCH_UID,
} from '../src/constants.ts';
import { jobsForDistrict } from '../src/office/ambient/ambientJobs.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { detectOffices } from '../src/office/layout/diOffices.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import {
  createDefaultLayout,
  getBlockedTiles,
  getDeskTiles,
  layoutToSeats,
  layoutToTileMap,
} from '../src/office/layout/layoutSerializer.ts';
import { electronicsTileSet, seatFacesElectronics } from '../src/office/layout/seatElectronics.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { Direction } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLDS = path.join(ROOT, 'packaging/di/worlds');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);

beforeAll(() => loadBundledCatalog());

const footprintOf: FootprintOf = (type) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};
const load = (name: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(WORLDS, `${name}.json`), 'utf8')) as OfficeLayout;
const facing = (s: { seatCol: number; seatRow: number; facingDir: number }): [number, number] =>
  s.facingDir === Direction.UP
    ? [s.seatCol, s.seatRow - 1]
    : s.facingDir === Direction.LEFT
      ? [s.seatCol - 1, s.seatRow]
      : s.facingDir === Direction.RIGHT
        ? [s.seatCol + 1, s.seatRow]
        : [s.seatCol, s.seatRow + 1];

/** The measured real office set (the home place and the two detected districts), never a substring. */
const REAL_OFFICES = ['Palace', 'Botanical Atrium', 'Deco Observatory'];

function detectDirect(layout: OfficeLayout, over: Partial<Parameters<typeof detectOffices>[0]> = {}) {
  const tileMap = layoutToTileMap(layout);
  const blocked = getBlockedTiles(layout.furniture);
  return detectOffices({
    layout,
    seats: layoutToSeats(layout.furniture),
    footprintOf,
    deskTiles: getDeskTiles(layout.furniture),
    electronicsTiles: electronicsTileSet(layout.furniture),
    isWalkable: (c, r) => isWalkable(c, r, tileMap, blocked),
    reservedSeatIds: new Set([DI_RECEPTION_BENCH_UID]),
    ...over,
  });
}

test('the new bounds are pinned to their exact values', () => {
  report('di_office_min_workstations', DI_OFFICE_MIN_WORKSTATIONS);
  report('di_office_max_offices', DI_OFFICE_MAX_OFFICES);
  assert.equal(DI_OFFICE_MIN_WORKSTATIONS, 12);
  assert.equal(DI_OFFICE_MAX_OFFICES, 8);
  assert.equal(DI_HOME_OFFICE_LABEL, 'Palace');
  assert.deepEqual(
    DI_OFFICE_DISTRICT_PATTERNS.map((p) => [p.match.source, p.short]),
    [
      ['atrium', 'Atrium'],
      ['observatory', 'Observatory'],
    ],
  );
});

test('every office-district pattern is one ambientJobs gives an OFFICE job row (the two lists cannot drift apart)', () => {
  for (const { short } of DI_OFFICE_DISTRICT_PATTERNS) {
    const jobs = jobsForDistrict(short);
    assert.ok(jobs.includes('receptionist'), `${short} has the office-district job row`);
    assert.notDeepEqual([...jobs], [...jobsForDistrict('Nowhere In Particular')], `${short} is not the default row`);
  }
});

test('living-metropolis: exactly the three offices, with the measured seat and workstation numbers', () => {
  const layout = load('living-metropolis');
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  const det = o.officeDetection!;
  assert.ok(det, 'detected');
  assert.deepEqual(
    det.offices.map((x) => x.label),
    REAL_OFFICES,
    'the real office set, in office order: home first, then the districts in the layout areas order',
  );
  assert.deepEqual(det.offices.map((x) => x.kind), ['home', 'district', 'district']);
  assert.deepEqual(det.offices.map((x) => x.seatIds.length), [124, 90, 90]);
  assert.deepEqual(det.offices.map((x) => x.workstationIds.length), [44, 72, 72], 'tier 1: desk-facing, reachable, facing electronics');
  assert.deepEqual(det.offices.map((x) => x.tableIds.length), [21, 18, 18], 'tier 2: desk-facing, reachable, no electronics');
  assert.deepEqual(det.offices.map((x) => x.chairIds.length), [59, 0, 0], 'tier 3: the Palace lounge chairs');
  assert.deepEqual(
    det.offices.map((x) => x.workstationIds.length + x.tableIds.length),
    [65, 90, 90],
    'workstations + tables is exactly what the previous definition called a workstation: the eligible seat set is unchanged',
  );
  assert.deepEqual([...det.reservedSeatIds], [DI_RECEPTION_BENCH_UID]);
  assert.equal(o.seats.size, 304, '125 + 90 + 90 chair seats, minus the receptionist\'s reserved bench seat');
  assert.ok(!o.seats.has(DI_RECEPTION_BENCH_UID), 'the reserved seat is not a seat');
  report('living_metropolis_offices', det.offices.map((x) => ({ label: x.label, seats: x.seatIds.length, workstations: x.workstationIds.length, tables: x.tableIds.length, chairs: x.chairIds.length })));
});

test('every workstation faces a desk AND electronics; a table seat faces a desk and NO electronics; the Palace lounge chairs face no desk; the tiers partition the eligible seats', () => {
  const layout = load('living-metropolis');
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  const det = o.officeDetection!;
  const desks = getDeskTiles(layout.furniture);
  const electronics = electronicsTileSet(layout.furniture);
  const seats = layoutToSeats(layout.furniture);
  for (const office of det.offices) {
    for (const uid of office.workstationIds) {
      const s = seats.get(uid)!;
      const [fc, fr] = facing(s);
      assert.ok(desks.has(`${fc},${fr}`), `${uid} faces a desk tile`);
      assert.ok(seatFacesElectronics(s, electronics), `${uid} faces electronics by the auto-on rule`);
    }
    for (const uid of office.tableIds) {
      const s = seats.get(uid)!;
      const [fc, fr] = facing(s);
      assert.ok(desks.has(`${fc},${fr}`), `${uid} faces a desk tile`);
      assert.ok(!seatFacesElectronics(s, electronics), `${uid} is a table seat: it faces no electronics`);
    }
    // the three tiers are disjoint and together are exactly the eligible seats of the office
    const all = [...office.workstationIds, ...office.tableIds, ...office.chairIds];
    assert.equal(new Set(all).size, all.length, `${office.label}: no seat is in two tiers`);
    assert.deepEqual([...all].sort(), [...office.seatIds].sort(), `${office.label}: the tiers are exactly the eligible seats`);
    if (office.kind === 'district')
      for (const uid of office.seatIds) {
        const s = seats.get(uid)!;
        assert.equal(layout.areaTiles![s.seatRow * layout.cols + s.seatCol], office.label, `${uid} is inside ${office.label}`);
      }
  }
  const palace = det.offices[0];
  assert.ok(palace.chairIds.length === 59);
  for (const uid of palace.chairIds) {
    const [fc, fr] = facing(seats.get(uid)!);
    assert.ok(!desks.has(`${fc},${fr}`), `${uid} is a lounge chair: it does not face a desk`);
  }
  // the 66 desk-facing Palace seats measured before the reservation = 44 workstations + 21 tables + the reserved bench
  assert.equal(palace.workstationIds.length + palace.tableIds.length + 1, 66);
  assert.ok(!det.offices.some((x) => [...x.workstationIds, ...x.tableIds].includes(DI_RECEPTION_BENCH_UID)), 'the reserved seat is in no tier');
});

test('what the satellites\' tables are: only the long and small meeting tables carry no electronics; the Palace tables include its lobby coffee tables', () => {
  const layout = load('living-metropolis');
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  const seats = layoutToSeats(layout.furniture);
  const faced = (uid: string) => {
    const [fc, fr] = facing(seats.get(uid)!);
    const piece = layout.furniture.find((f) => {
      const e = getCatalogEntry(f.type);
      return !!e && e.isDesk && fc >= f.col && fc < f.col + e.footprintW && fr >= f.row && fr < f.row + e.footprintH;
    });
    return piece?.type ?? 'none';
  };
  const tally = (ids: readonly string[]) => {
    const m: Record<string, number> = {};
    for (const id of ids) m[faced(id)] = (m[faced(id)] ?? 0) + 1;
    return m;
  };
  const [palace, atrium, observatory] = o.officeDetection!.offices;
  assert.deepEqual(tally(palace.workstationIds), { DESK_FRONT: 40, TABLE_FRONT: 4 });
  assert.deepEqual(tally(palace.tableIds), { COFFEE_TABLE: 8, TABLE_FRONT: 12, SMALL_TABLE_FRONT: 1 });
  for (const office of [atrium, observatory]) {
    assert.deepEqual(tally(office.workstationIds), { DESK_FRONT: 72 });
    assert.deepEqual(tally(office.tableIds), { TABLE_FRONT: 12, SMALL_TABLE_FRONT: 6 });
  }
});

test('with no electronics anywhere every desk-facing seat is a table seat; detection and the eligible seats do not change', () => {
  const layout = load('living-metropolis');
  const withEl = detectDirect(layout)!;
  const without = detectDirect(layout, { electronicsTiles: new Set() })!;
  assert.deepEqual(without.offices.map((x) => x.label), withEl.offices.map((x) => x.label), 'the same offices are detected');
  assert.deepEqual(without.offices.map((x) => [...x.seatIds]), withEl.offices.map((x) => [...x.seatIds]), 'the eligible seat set is unchanged');
  assert.deepEqual(without.offices.map((x) => x.workstationIds.length), [0, 0, 0]);
  assert.deepEqual(without.offices.map((x) => x.tableIds.length), [65, 90, 90]);
  assert.deepEqual([...without.officeIndexBySeat], [...withEl.officeIndexBySeat], 'every seat keeps its office');
});

test('seatFacesElectronics: depth and flank boundaries of the shared auto-on rule', () => {
  const seat = (facingDir: Direction) => ({ seatCol: 10, seatRow: 10, facingDir });
  const tiles = (...ts: Array<[number, number]>) => new Set(ts.map(([c, r]) => `${c},${r}`));
  const D = AUTO_ON_FACING_DEPTH;
  assert.equal(seatFacesElectronics(seat(Direction.DOWN), tiles([10, 10 + D])), true, 'straight ahead at the last depth');
  assert.equal(seatFacesElectronics(seat(Direction.DOWN), tiles([10, 10 + D + 1])), false, 'one beyond the depth');
  assert.equal(seatFacesElectronics(seat(Direction.DOWN), tiles([9, 11])), true, 'one tile beside the first tile ahead');
  assert.equal(seatFacesElectronics(seat(Direction.DOWN), tiles([8, 11])), false, 'two tiles beside');
  assert.equal(seatFacesElectronics(seat(Direction.DOWN), tiles([10, 9])), false, 'behind the seat');
  assert.equal(seatFacesElectronics(seat(Direction.UP), tiles([11, 10 - D])), true, 'facing up, flank at the last depth');
  assert.equal(seatFacesElectronics(seat(Direction.LEFT), tiles([10 - D, 11])), true, 'facing left, flank');
  assert.equal(seatFacesElectronics(seat(Direction.RIGHT), tiles([10 + 1, 9])), true, 'facing right, flank');
  assert.equal(seatFacesElectronics(seat(Direction.RIGHT), new Set()), false, 'nothing ahead');
});

test('office-like chairs outside an office district are never offices: a relabelled district, and a district whose desks are gone', () => {
  const layout = load('living-metropolis');
  // (1) the Atrium relabelled as a café-like district: 90 desk-facing seats, but its label is not an office district
  const relabelled: OfficeLayout = {
    ...layout,
    areas: layout.areas!.map((a) => (a.label === 'Botanical Atrium' ? { ...a, label: 'Corner Cafe' } : a)),
    areaTiles: layout.areaTiles!.map((l) => (l === 'Botanical Atrium' ? 'Corner Cafe' : l)),
  };
  const r1 = detectDirect(relabelled)!;
  assert.deepEqual(r1.offices.map((x) => x.label), ['Palace', 'Deco Observatory'], 'the relabelled district is not an office');
  // (2) the Atrium keeps its label but its chairs no longer face desks (café-style seating): not enough workstations
  const noDesks = getDeskTiles(layout.furniture);
  const seats = layoutToSeats(layout.furniture);
  for (const s of seats.values())
    if (layout.areaTiles![s.seatRow * layout.cols + s.seatCol] === 'Botanical Atrium') {
      const [fc, fr] = facing(s);
      noDesks.delete(`${fc},${fr}`);
    }
  const r2 = detectDirect(layout, { deskTiles: noDesks })!;
  assert.deepEqual(r2.offices.map((x) => x.label), ['Palace', 'Deco Observatory']);
});

test('the threshold is exact: an office district needs DI_OFFICE_MIN_WORKSTATIONS desk-facing seats (11 is not enough, 12 is)', () => {
  const layout = load('living-metropolis');
  const seats = layoutToSeats(layout.furniture);
  const atrium = [...seats.values()].filter((s) => layout.areaTiles![s.seatRow * layout.cols + s.seatCol] === 'Botanical Atrium');
  const keep = (n: number) => {
    const desks = getDeskTiles(layout.furniture);
    for (const s of atrium.slice(n)) desks.delete(facing(s).join(','));
    // seats that share a desk tile with a kept seat still face a desk, so count what actually remains
    return desks;
  };
  const count = (n: number) => {
    const desks = keep(n);
    return atrium.filter((s) => desks.has(facing(s).join(','))).length;
  };
  // find the smallest cut that leaves exactly 11 and exactly 12 desk-facing Atrium seats
  let eleven = -1;
  let twelve = -1;
  for (let n = 0; n <= atrium.length; n++) {
    if (count(n) === 11 && eleven < 0) eleven = n;
    if (count(n) === 12 && twelve < 0) twelve = n;
  }
  assert.ok(eleven >= 0 && twelve >= 0, 'the layout can be cut to exactly 11 and exactly 12 desk-facing Atrium seats');
  assert.deepEqual(detectDirect(layout, { deskTiles: keep(eleven) })!.offices.map((x) => x.label), ['Palace', 'Deco Observatory']);
  assert.deepEqual(detectDirect(layout, { deskTiles: keep(twelve) })!.offices.map((x) => x.label), REAL_OFFICES);
});

test('an office district the home cannot walk to is not an office (reachability), and home-only detection is null', () => {
  const layout = load('living-metropolis');
  const tileMap = layoutToTileMap(layout);
  const blocked = getBlockedTiles(layout.furniture);
  const atriumBox = detectDirect(layout)!.offices[1].box;
  const sealed = detectDirect(layout, {
    isWalkable: (c, r) =>
      !(c >= atriumBox.c0 - 1 && c < atriumBox.c1 + 1 && r >= atriumBox.r0 - 1 && r < atriumBox.r1 + 1) &&
      isWalkable(c, r, tileMap, blocked),
  })!;
  assert.deepEqual(sealed.offices.map((x) => x.label), ['Palace', 'Deco Observatory']);
  // the observatory sealed too: only the home is left, which is not a detection at all
  const obsBox = detectDirect(layout)!.offices[2].box;
  const homeOnly = detectDirect(layout, {
    isWalkable: (c, r) =>
      !(c >= atriumBox.c0 - 1 && c < atriumBox.c1 + 1 && r >= atriumBox.r0 - 1 && r < atriumBox.r1 + 1) &&
      !(c >= obsBox.c0 - 1 && c < obsBox.c1 + 1 && r >= obsBox.r0 - 1 && r < obsBox.r1 + 1) &&
      isWalkable(c, r, tileMap, blocked),
  });
  assert.equal(homeOnly, null, 'a home with no office district keeps the legacy seating');
});

test('expanded-office, vast-forest and metropolis are NOT detected: they keep homeSeats exactly (minus only the receptionist\'s reserved seat)', () => {
  for (const name of ['expanded-office', 'vast-forest', 'metropolis']) {
    const layout = load(name);
    const legacy = new OfficeState(layout); // the DI office off: Generic Pixel seating
    const di = new OfficeState(layout);
    di.setReceptionistEnabled(true);
    assert.equal(di.officeDetection, null, `${name}: no offices detected`);
    assert.deepEqual(
      [...di.seats.keys()].sort(),
      [...legacy.seats.keys()].filter((u) => !di.reservedSeatIds.has(u)).sort(),
      `${name}: the legacy seats minus the reserved one`,
    );
    assert.deepEqual([...di.reservedSeatIds], [DI_RECEPTION_BENCH_UID]);
    assert.equal(di.seats.size, legacy.seats.size - 1);
  }
});

test('the bundled default layout has no post and no offices: its seats and blocked tiles are exactly what they always were', () => {
  const off = new OfficeState(createDefaultLayout());
  const on = new OfficeState(createDefaultLayout());
  on.setReceptionistEnabled(true);
  assert.equal(on.officeDetection, null);
  assert.equal(on.receptionPost, null);
  assert.deepEqual([...on.seats.keys()], [...off.seats.keys()]);
  assert.deepEqual([...on.blockedTiles].sort(), [...off.blockedTiles].sort());
  assert.equal(on.reservedSeatIds.size, 0);
});

test('Generic Pixel (the DI office switch off) is unchanged: every home seat, no detection, no reservation, no blocked stand tile', () => {
  const layout = load('living-metropolis');
  const o = new OfficeState(layout);
  assert.equal(o.officeDetection, null);
  assert.equal(o.seats.size, 125);
  assert.ok(o.seats.has(DI_RECEPTION_BENCH_UID));
  assert.equal(o.receptionPost, null);
  assert.deepEqual([...o.blockedTiles].sort(), [...getBlockedTiles(layout.furniture)].sort());
  // and switching the DI office on and off again returns to exactly that
  o.setReceptionistEnabled(true);
  o.setReceptionistEnabled(false);
  assert.equal(o.seats.size, 125);
  assert.equal(o.officeDetection, null);
  assert.deepEqual([...o.blockedTiles].sort(), [...getBlockedTiles(layout.furniture)].sort());
});

test('detection is computed ONCE per layout and well inside a time bound; it is not recomputed by the simulation', () => {
  const layout = load('living-metropolis');
  const started = performance.now();
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  const built = performance.now() - started;
  const det = o.officeDetection;
  for (let i = 0; i < 200; i++) o.update(0.1);
  assert.equal(o.officeDetection, det, 'the same object: not recomputed per frame');
  const t0 = performance.now();
  for (let i = 0; i < 5; i++) detectDirect(layout);
  const each = (performance.now() - t0) / 5;
  report('detection_ms', { buildIncludingState: Math.round(built), eachDetection: Math.round(each) });
  assert.ok(each < 750, `detection over the 352x312 world stays within the bound (${Math.round(each)} ms)`);
});

test('the read-only assignment export reports the offices and their seat lists with no characters', () => {
  const o = new OfficeState(load('living-metropolis'));
  o.setReceptionistEnabled(true);
  const a = o.officeAssignment()!;
  assert.deepEqual(a.offices.map((x) => [x.label, x.seats, x.workstations, x.otherChairs]), [
    ['Palace', 124, 44, 59],
    ['Botanical Atrium', 90, 72, 0],
    ['Deco Observatory', 90, 72, 0],
  ]);
  assert.deepEqual(a.offices.map((x) => x.tables), [21, 18, 18], 'the table seats are reported too');
  assert.deepEqual(a.reservedSeatIds, [DI_RECEPTION_BENCH_UID]);
  assert.deepEqual(a.characters, []);
  assert.equal(new OfficeState(load('metropolis')).officeAssignment(), null);
});
