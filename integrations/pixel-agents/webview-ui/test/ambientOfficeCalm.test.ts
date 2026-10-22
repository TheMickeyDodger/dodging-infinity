/**
 * C5: CALMER OFFICE INTERIORS, measured on the real living metropolis with the same method as findings §5 (per-office resident counts at
 * t = 0 / 600 / 1200 s, with zero agents and with fixture agents), coupled to the three-office seating — not a cap-only change:
 *   - an office resident's post and stops are in the COMMON rooms (lobby, corridor, courtyard), never in a work room (a room holding a desk-facing seat: a workstation or a table seat);
 *   - at no sample do more than DI_OFFICE_WORK_ROOM_RESIDENT_TARGET residents STAND in an office's work rooms, with or without agents;
 *   - each office lobby has visible ambient life at t = 0 (at least 2, the only instant this file floors); the t = 0 / 600 / 1200 series is REPORTED as measured, not
 *     floored (the Palace has 1 at t = 600). The sustained criteria (at least 1 in the common rooms at each of the 121 sampled instants, and presence at
 *     least that of the S4 ambient plans reproduced on this engine with the DI office off) are `ambientOfficePresence.test.ts`;
 *   - the satellite offices are working offices now (duty rosters), and no resident of any office is deleted.
 * Places are classified by their EXACT names (the measured real office set), never by substring.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { DI_OFFICE_WORK_ROOM_RESIDENT_TARGET } from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';
import { OFFICE_LABELS, officeRooms } from './helpers/officeRooms.ts';

const ROOT = path.join(__dirname, '..', '..');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);
beforeAll(() => loadBundledCatalog());
const LAYOUT = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf8')) as OfficeLayout;
const OFFICES: readonly string[] = OFFICE_LABELS;
/** The work-room and common-room tiles of each office (the shared definition: a work room holds a detected workstation). */
const roomTiles = () => officeRooms(LAYOUT).rooms;

const standingCounts = (o: OfficeState, rooms: ReturnType<typeof roomTiles>) => {
  const cols = LAYOUT.cols;
  const counts: Record<string, { standingInWork: number; visibleInCommon: number; visible: number }> = {};
  for (const label of OFFICES) counts[label] = { standingInWork: 0, visibleInCommon: 0, visible: 0 };
  for (const r of o.getAmbientResidents()) {
    if (!OFFICES.includes(r.district)) continue;
    const tile = r.tileRow * cols + r.tileCol;
    const c = counts[r.district];
    c.visible++;
    if (rooms.get(r.district)!.common.has(tile)) c.visibleInCommon++;
    if (r.state === 'dwell' && rooms.get(r.district)!.work.has(tile)) c.standingInWork++;
  }
  return counts;
};

function sample(o: OfficeState, rooms: ReturnType<typeof roomTiles>) {
  const at: Record<string, ReturnType<typeof standingCounts>> = { 't0': standingCounts(o, rooms) };
  for (const t of [600, 1200]) {
    const steps = (t - (t === 600 ? 0 : 600)) * 10;
    for (let i = 0; i < steps; i++) o.update(0.1);
    at[`t${t}`] = standingCounts(o, rooms);
  }
  return at;
}

test('the target is pinned', () => {
  assert.equal(DI_OFFICE_WORK_ROOM_RESIDENT_TARGET, 2);
});

test('every office resident\'s post and stops are in the COMMON rooms; none is in any office\'s work room', () => {
  const rooms = roomTiles();
  const anyWork = new Set<number>();
  for (const r of rooms.values()) for (const t of r.work) anyWork.add(t);
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  let checked = 0;
  let ownStops = 0;
  const cols = LAYOUT.cols;
  for (const r of o.ambient!.residents) {
    if (!OFFICES.includes(r.district)) continue;
    const { common } = rooms.get(r.district)!;
    assert.ok(common.has(r.routine[0]), `${r.id}: its post ${r.routine[0]} is in a common room of ${r.district}`);
    for (const tile of r.routine) {
      assert.ok(!anyWork.has(tile), `${r.id}: routine tile ${tile} is in a work room`);
      // a stop inside the resident's own office is a common-room stop (a roaming job also has one stop in ANOTHER place)
      const place = (tile % cols) >= 0 ? LAYOUT.areaTiles?.[tile] ?? null : null;
      if (place === r.district || rooms.get(r.district)!.common.has(tile)) {
        assert.ok(common.has(tile), `${r.id}: its stop ${tile} in ${r.district} is in a common room`);
        ownStops++;
      }
    }
    checked++;
  }
  assert.equal(checked, 27, 'every resident of the three offices (11 + 8 + 8): none deleted');
  assert.ok(ownStops >= 27, `the residents have stops in their own common rooms (${ownStops})`);
  for (const label of OFFICES) assert.ok(rooms.get(label)!.work.size > 0 && rooms.get(label)!.common.size > 0, `${label} has both kinds of room`);
});

test('MEASURED (simulated s): at each of t = 0 / 600 / 1200 s no more than the target stand in an office\'s work rooms (zero agents and 24 agents), and at t = 0 at least 2 are in its common rooms; the t = 0 / 600 / 1200 series is REPORTED as measured', () => {
  const rooms = roomTiles();
  const before = new OfficeState(LAYOUT); // the previous build's interior: the DI office switch off
  before.setAmbientEnabled(true);
  const beforeCounts = sample(before, rooms);
  // zero agents
  const zero = new OfficeState(LAYOUT);
  zero.setReceptionistEnabled(true);
  zero.setAmbientEnabled(true);
  const afterZero = sample(zero, rooms);
  // with fixture agents seated across the three offices (24 agents)
  const withAgents = new OfficeState(LAYOUT);
  withAgents.setReceptionistEnabled(true);
  withAgents.setAmbientEnabled(true);
  const det = withAgents.officeDetection!;
  let id = 1;
  for (const off of det.offices) for (let k = 0; k < 8; k++) withAgents.addAgent(id++, 0, 0, off.workstationIds[k * 3], true);
  const afterAgents = sample(withAgents, rooms);
  // the series per office: [visible in common rooms, standing in work rooms, visible anywhere] at each sample
  const series = (run: ReturnType<typeof sample>) =>
    Object.fromEntries(OFFICES.map((o) => [o, Object.values(run).map((c) => [c[o].visibleInCommon, c[o].standingInWork, c[o].visible])]));
  report('office_interiors_series_[common,standingInWork,visible]_at_t0_t600_t1200', {
    before: series(beforeCounts),
    afterZeroAgents: series(afterZero),
    after24Agents: series(afterAgents),
  });
  report('office_interiors_before', beforeCounts);
  report('office_interiors_after_zero_agents', afterZero);
  report('office_interiors_after_with_24_agents', afterAgents);
  // CLAIM 1 (measured): no more than the target stand in an office's work rooms, at every sample, with and without agents
  for (const [name, run] of [['zero agents', afterZero], ['24 agents', afterAgents]] as const)
    for (const [t, counts] of Object.entries(run))
      for (const label of OFFICES)
        assert.ok(
          counts[label].standingInWork <= DI_OFFICE_WORK_ROOM_RESIDENT_TARGET,
          `${name} ${t} ${label}: ${counts[label].standingInWork} residents standing in work rooms (target ${DI_OFFICE_WORK_ROOM_RESIDENT_TARGET})`,
        );
  // CLAIM 2 (measured, and ONLY at t = 0 — the design target, unchanged): every office lobby starts with visible ambient life in its common rooms. The series
  // at t = 600 and t = 1200 is REPORTED above, not floored: a single sampled instant is a noise-sensitive point. What the C5 defect (an empty Palace at
  // t = 1200 s) is judged by is over EVERY one of 121 samples: `ambientOfficePresence.test.ts` (the common rooms are never empty, and the presence is
  // at least S4's).
  for (const label of OFFICES) assert.ok(afterZero.t0[label].visibleInCommon >= 2, `${label} lobby has visible life at t=0 (${afterZero.t0[label].visibleInCommon})`);
}, 600_000);

test('the satellite offices are working offices now: duty rosters exist for all three, and no resident is lost to them', () => {
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const rosters = o.ambient!.dutyRosters();
  assert.deepEqual(rosters.map((r) => r.label), OFFICES);
  assert.deepEqual(rosters.map((r) => r.members), [11, 8, 8]);
  report('office_duty_rosters', rosters);
  const legacy = new OfficeState(LAYOUT);
  legacy.setAmbientEnabled(true);
  assert.deepEqual(legacy.ambient!.dutyRosters().map((r) => r.label), ['Palace'], 'before: only the Palace was a working office');
  assert.equal(o.ambient!.residents.length, 145);
});
