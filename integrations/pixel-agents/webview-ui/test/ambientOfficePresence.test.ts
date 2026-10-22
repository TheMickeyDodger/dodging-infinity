/**
 * C5 — THE OFFICES KEEP THEIR PEOPLE, judged RELATIVE to the previous build (S4), from ACTUAL PHYSICAL POSITIONS.
 *
 * Presence = a resident of the office standing or walking on a tile that belongs to one of the office's rooms (tile and room membership, not
 * affiliation and not standing-only), sampled every 10 SIMULATED seconds over 1200 s (121 samples), with zero agents and with 24 agents:
 *   - S4 (the DI office off) counts every office room: its residents also lingered in the work rooms (standing and walking);
 *   - the candidate counts the NON-work rooms only, and must be at least S4's time-averaged presence in every detected office, while STANDING in work rooms
 *     stays at or below the target (it is 0) and below S4's.
 * The 24-agent S4 figure is not one number: idle agents wander with Math.random, and S4's residents get caught up among them in the work rooms, so the
 * baseline is the MEAN over the fixed seeds 1..12 (Math.random seeded per run); the candidate never meets the agents, so it is the same under every seed.
 * Also pinned here: the visit roster (every 4th roaming office resident makes the cross-world visit) and the posted replacement of the Palace's former
 * receptionist slots. The remote residents are pinned byte-equal in `ambientPlanGolden.test.ts`.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test, vi } from 'vitest';

import { AMBIENT_OFFICE_VISIT_EVERY, DI_OFFICE_WORK_ROOM_RESIDENT_TARGET } from '../src/constants.ts';
import { AMBIENT_JOBS } from '../src/office/ambient/ambientJobs.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';
import { OFFICE_LABELS, officeRooms } from './helpers/officeRooms.ts';

const ROOT = path.join(__dirname, '..', '..');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);
beforeAll(() => loadBundledCatalog());
const LAYOUT = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf8')) as OfficeLayout;
const SAMPLES = 121; // t = 0, 10, ..., 1200 simulated seconds
const SEEDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

/** mulberry32: a small seeded generator for the agents' idle wandering (they use Math.random) */
function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let geo: ReturnType<typeof officeRooms> | null = null;
/** the office rooms and workstations, computed once (needs the loaded catalog) */
const geometry = () => (geo ??= officeRooms(LAYOUT));

interface Presence {
  /** time-averaged residents of the office physically in ANY of its rooms (work + non-work) */
  inOffice: number;
  /** ... in its non-work rooms */
  nonWork: number;
  standingMax: number;
  standingAvg: number;
  /** the fewest residents of the office in its non-work rooms at any one of the 121 samples */
  minNonWork: number;
  /** residents in the non-work rooms at t = 0 / 600 / 1200 */
  at: [number, number, number];
}

function measure(diOffice: boolean, agents: 0 | 24, seed: number): Record<string, Presence> {
  const { rooms: roomsOf, workstations: ws } = geometry();
  const spy = vi.spyOn(Math, 'random').mockImplementation(seeded(seed));
  try {
    const o = new OfficeState(LAYOUT);
    if (diOffice) o.setReceptionistEnabled(true);
    o.setAmbientEnabled(true);
    if (agents === 24) {
      let id = 1;
      for (const seats of ws) for (let k = 0; k < 8; k++) o.addAgent(id++, 0, 0, seats[k * 3], true);
    }
    const series: Record<string, Array<[number, number, number]>> = Object.fromEntries(OFFICE_LABELS.map((l) => [l, []]));
    const sample = () => {
      const acc: Record<string, [number, number, number]> = Object.fromEntries(OFFICE_LABELS.map((l) => [l, [0, 0, 0]]));
      for (const r of o.getAmbientResidents()) {
        const room = roomsOf.get(r.district);
        if (!room) continue;
        const tile = r.tileRow * LAYOUT.cols + r.tileCol;
        if (room.work.has(tile)) {
          acc[r.district][0]++;
          if (r.state === 'dwell') acc[r.district][2]++;
        } else if (room.common.has(tile)) acc[r.district][1]++;
      }
      for (const l of OFFICE_LABELS) series[l].push(acc[l]);
    };
    sample();
    for (let s = 1; s < SAMPLES; s++) {
      for (let i = 0; i < 100; i++) o.update(0.1);
      sample();
    }
    const avg = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
    return Object.fromEntries(
      OFFICE_LABELS.map((l) => {
        const s = series[l];
        return [
          l,
          {
            inOffice: avg(s.map((x) => x[0] + x[1])),
            nonWork: avg(s.map((x) => x[1])),
            standingMax: Math.max(...s.map((x) => x[2])),
            standingAvg: avg(s.map((x) => x[2])),
            minNonWork: Math.min(...s.map((x) => x[1])),
            at: [s[0][1], s[60][1], s[120][1]],
          } satisfies Presence,
        ];
      }),
    );
  } finally {
    spy.mockRestore();
  }
}

const r2 = (n: number) => Math.round(n * 100) / 100;

test('MEASURED: in every detected office the time-averaged PHYSICAL presence in the non-work rooms is at least S4\'s physical presence in the whole office (0 agents, and 24 agents against the 12-seed mean); work rooms stay clear', () => {
  const s4Zero = measure(false, 0, 1);
  const s4Agents = SEEDS.map((seed) => measure(false, 24, seed));
  const newZero = measure(true, 0, 1);
  const newAgents = measure(true, 24, 1);
  const newAgentsOtherSeed = measure(true, 24, 7);
  const table: Record<string, unknown> = {};
  // the table is REPORTED first, then judged: a shortfall shows its numbers
  const problems: string[] = [];
  const check = (ok: boolean, message: string) => {
    if (!ok) problems.push(message);
  };
  for (const l of OFFICE_LABELS) {
    const s4Mean = s4Agents.reduce((a, m) => a + m[l].inOffice, 0) / SEEDS.length;
    const s4Range = [Math.min(...s4Agents.map((m) => m[l].inOffice)), Math.max(...s4Agents.map((m) => m[l].inOffice))];
    table[l] = {
      s4_0_agents: { inOffice: r2(s4Zero[l].inOffice), nonWork: r2(s4Zero[l].nonWork), standingAvg: r2(s4Zero[l].standingAvg), standingMax: s4Zero[l].standingMax, minNonWork: s4Zero[l].minNonWork, at: s4Zero[l].at },
      s4_24_agents_mean_of_12_seeds: { inOffice: r2(s4Mean), range: s4Range.map(r2) },
      candidate_0_agents: { nonWork: r2(newZero[l].nonWork), inOffice: r2(newZero[l].inOffice), standingAvg: r2(newZero[l].standingAvg), standingMax: newZero[l].standingMax, minNonWork: newZero[l].minNonWork, at: newZero[l].at },
      candidate_24_agents: { nonWork: r2(newAgents[l].nonWork), inOffice: r2(newAgents[l].inOffice), standingMax: newAgents[l].standingMax, minNonWork: newAgents[l].minNonWork, at: newAgents[l].at },
    };
    // the relative claim, per office and per agent count
    check(newZero[l].nonWork >= s4Zero[l].inOffice, `${l} 0 agents: non-work presence ${r2(newZero[l].nonWork)} >= S4 in-office ${r2(s4Zero[l].inOffice)}`);
    check(newAgents[l].nonWork >= s4Mean, `${l} 24 agents: non-work presence ${r2(newAgents[l].nonWork)} >= S4 in-office mean ${r2(s4Mean)} (12 seeds)`);
    // work rooms stay clear: standing is within the target at every sample and below S4's average standing
    for (const m of [newZero, newAgents]) {
      check(m[l].standingMax <= DI_OFFICE_WORK_ROOM_RESIDENT_TARGET, `${l}: ${m[l].standingMax} standing in work rooms (target ${DI_OFFICE_WORK_ROOM_RESIDENT_TARGET})`);
      check(m[l].standingAvg < s4Zero[l].standingAvg, `${l}: standing in work rooms ${r2(m[l].standingAvg)} < S4's ${r2(s4Zero[l].standingAvg)}`);
    }
    // the candidate's residents never meet the agents (they keep to the common rooms), so the agents' wandering cannot change their presence
    check(r2(newAgentsOtherSeed[l].nonWork) === r2(newAgents[l].nonWork), `${l}: the same under another wander seed`);
    check(r2(newAgents[l].nonWork) === r2(newZero[l].nonWork), `${l}: the same with and without agents`);
    // THE DEFECT (the Palace read empty at t = 1200 s), judged physically: min common-room presence >= 1 at each of the 121 sampled instants (every 10
    // simulated s over 1200 s), with and without agents — a statement about those samples, not a universal one; and the START target is unchanged: at
    // least 2 of them at t = 0
    for (const m of [newZero, newAgents]) {
      check(m[l].minNonWork >= 1, `${l}: the fewest residents in its non-work rooms at any sample is ${m[l].minNonWork} (>= 1)`);
      check(m[l].at[0] >= 2, `${l}: ${m[l].at[0]} residents in its non-work rooms at t = 0 (>= 2)`);
    }
  }
  report('c5_physical_presence_table_[avg_residents_in_rooms;121_samples;1200_simulated_s]', table);
  assert.deepEqual(problems, [], 'every relative claim holds');
}, 900_000);

test('PINNED: every 4th roaming resident of a detected office makes the cross-world visit; posted residents never leave; everyone else keeps the office', () => {
  assert.equal(AMBIENT_OFFICE_VISIT_EVERY, 4);
  const { rooms: roomsOf } = geometry();
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const expected: Record<string, { roamers: number; visiting: number }> = {
    Palace: { roamers: 8, visiting: 2 },
    'Botanical Atrium': { roamers: 4, visiting: 1 },
    'Deco Observatory': { roamers: 5, visiting: 2 },
  };
  for (const label of OFFICE_LABELS) {
    const room = roomsOf.get(label)!;
    const inOffice = (t: number) => room.work.has(t) || room.common.has(t);
    const mine = o.ambient!.residents.filter((r) => r.district === label);
    const roamers = mine.filter((r) => AMBIENT_JOBS[r.job].style === 'roaming');
    const visiting = roamers.filter((r) => [...r.routine].some((t) => !inOffice(t)));
    assert.equal(roamers.length, expected[label].roamers, `${label}: roaming residents`);
    assert.equal(visiting.length, expected[label].visiting, `${label}: roamers who visit`);
    // exactly the roamers at roster positions 0, N, 2N, ... (the roster is the resident order of the place)
    assert.deepEqual(
      roamers.map((r, i) => [r.id, i % AMBIENT_OFFICE_VISIT_EVERY === 0]),
      roamers.map((r) => [r.id, visiting.includes(r)]),
      `${label}: the visitors are the roamers at roster positions 0, ${AMBIENT_OFFICE_VISIT_EVERY}, ${2 * AMBIENT_OFFICE_VISIT_EVERY}, ...`,
    );
    // a visitor's last stop is the one outside; everything else about every office resident stays in the office
    for (const r of visiting) assert.equal([...r.routine].filter((t) => !inOffice(t)).length, 1, `${r.id}: exactly one stop outside the office`);
    for (const r of mine.filter((x) => !visiting.includes(x))) assert.ok([...r.routine].every(inOffice), `${r.id}: every stop inside the office`);
    for (const r of mine.filter((x) => AMBIENT_JOBS[x.job].style === 'posted')) assert.ok([...r.routine].every(inOffice), `${r.id}: a posted resident never leaves`);
  }
  report('c5_visit_roster', Object.fromEntries(OFFICE_LABELS.map((l) => [l, expected[l]])));
});

test('PINNED: the Palace\'s former reception slots are POSTED gardeners (no ambient receptionist at the post); the resident count and every other slot are unchanged', () => {
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const palace = o.ambient!.residents.filter((r) => r.district === 'Palace');
  assert.equal(palace.length, 11, 'the Palace keeps its 11 residents');
  assert.deepEqual(palace.filter((r) => r.job === 'gardener').map((r) => r.id), ['amb-palace-gardener-1', 'amb-palace-gardener-2', 'amb-palace-gardener-3']);
  assert.equal(AMBIENT_JOBS.gardener.style, 'posted');
  assert.equal(palace.filter((r) => r.job === 'receptionist').length, 0);
  assert.deepEqual(
    palace.map((r) => r.job).sort(),
    ['courier', 'courier', 'custodian', 'custodian', 'gardener', 'gardener', 'gardener', 'office-visitor', 'office-visitor', 'office-visitor', 'office-visitor'],
  );
});
