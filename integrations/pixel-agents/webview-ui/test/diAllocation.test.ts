/**
 * The three-office allocation (office/layout/diAllocation.ts), proven against the rule written in .herd/state/exec-p2bc.md §C3: quotas over
 * WORKSTATION (tier 1) capacity (floor + remainder, capped capacity redistributed), two-pass placement (pass 1 reserves every held / preferred / vouched
 * seat before anyone fresh is placed), workstation-first with the honest "table, not workstation" and then "chair, not workstation" fallbacks, and no
 * churn. Every worked case in the design is asserted here step by step, not only its final counts.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import type { AllocCharacter, AllocInput, AllocOffice } from '../src/office/layout/diAllocation.ts';
import { allocateDiSeats, officeQuotas } from '../src/office/layout/diAllocation.ts';

// ── quotas ────────────────────────────────────────────────────────────────────────────────────────────────

const sum = (a: readonly number[]) => a.reduce((x, y) => x + y, 0);
function checkQuotas(n: number, caps: number[]): number[] {
  const q = officeQuotas(n, caps);
  assert.equal(q.length, caps.length);
  assert.equal(sum(q), Math.min(n, sum(caps)), `Σq === min(N, Σcap) for n=${n} caps=${caps}`);
  q.forEach((v, i) => assert.ok(v >= 0 && v <= caps[i], `0 <= q[${i}]=${v} <= cap ${caps[i]} for n=${n} caps=${caps}`));
  assert.deepEqual(officeQuotas(n, caps), q, 'deterministic');
  return q;
}

test('quotas: the actual full workstation case fills every workstation exactly (n=188, tier-1 caps 44/72/72)', () => {
  assert.deepEqual(checkQuotas(188, [44, 72, 72]), [44, 72, 72]);
});

test('quotas: over the real tier-1 capacities the first examples are 1/0/0, 1/1/0, 1/1/1, 2/1/1 and 90 is capped by the Palace to 18/36/36', () => {
  const real = [44, 72, 72];
  assert.deepEqual(checkQuotas(1, real), [1, 0, 0]);
  assert.deepEqual(checkQuotas(2, real), [1, 1, 0]);
  assert.deepEqual(checkQuotas(3, real), [1, 1, 1]);
  assert.deepEqual(checkQuotas(4, real), [2, 1, 1]);
  assert.deepEqual(checkQuotas(39, real), [13, 13, 13]);
  assert.deepEqual(checkQuotas(150, real), [44, 53, 53], 'the Palace is capped at its 44 workstations and the rest is shared by the satellites');
  assert.deepEqual(checkQuotas(300, real), [44, 72, 72], 'N above Σ tier-1 is capped to Σ tier-1: tables and chairs never raise a quota');
});

test('quotas: strongly skewed capacities never leave a short quota', () => {
  assert.deepEqual(checkQuotas(300, [0, 0, 300]), [0, 0, 300]);
  assert.deepEqual(checkQuotas(1, [1, 1, 500]), [1, 0, 0]);
  assert.deepEqual(checkQuotas(7, [0, 5, 0]), [0, 5, 0], 'N above the only capacity is capped to it');
  checkQuotas(500, [1, 1, 500]);
  checkQuotas(2, [0, 0, 300]);
});

test('quotas: the worked cases of the design, N = 1, 2, 3, 4, 7, the capped 2/4/3 example, N above capacity, one office', () => {
  const big = [1000, 1000, 1000];
  assert.deepEqual(checkQuotas(1, big), [1, 0, 0]);
  assert.deepEqual(checkQuotas(2, big), [1, 1, 0]);
  assert.deepEqual(checkQuotas(3, big), [1, 1, 1]);
  assert.deepEqual(checkQuotas(4, big), [2, 1, 1]);
  assert.deepEqual(checkQuotas(7, big), [3, 2, 2]);
  assert.deepEqual(checkQuotas(9, [2, 10, 10]), [2, 4, 3], 'Palace over capacity by 1: the excess goes to the office after it');
  assert.deepEqual(checkQuotas(8, [2, 2, 2]), [2, 2, 2], 'N above Σcap is capped to Σcap');
  assert.deepEqual(checkQuotas(5, [10]), [5], 'one office takes everything');
  assert.deepEqual(checkQuotas(0, big), [0, 0, 0]);
  assert.deepEqual(officeQuotas(3, []), []);
});

test('quotas: a deterministic sweep over every small (n, capacity) combination keeps the invariants', () => {
  let cases = 0;
  for (let e = 1; e <= 4; e++) {
    const caps: number[] = new Array(e).fill(0);
    const total = 6 ** e;
    for (let code = 0; code < total; code++) {
      let c = code;
      for (let i = 0; i < e; i++) {
        caps[i] = c % 6;
        c = Math.floor(c / 6);
      }
      for (let n = 0; n <= sum(caps) + 3; n++) {
        checkQuotas(n, [...caps]);
        cases++;
      }
    }
  }
  assert.ok(cases > 20000, `the sweep covered ${cases} combinations`);
});

// ── placement fixtures ───────────────────────────────────────────────────────────────────────────────────

/** Offices of workstations `W<office>-<k>`, table seats `T<office>-<k>` and chairs `C<office>-<k>`, on a grid, each office 100 columns apart. */
function world(caps: number[], chairs: number[] = caps.map(() => 0), tables: number[] = caps.map(() => 0)) {
  const seatPos = new Map<string, { col: number; row: number }>();
  const offices: AllocOffice[] = caps.map((cap, o) => {
    const workstationIds: string[] = [];
    const tableIds: string[] = [];
    const chairIds: string[] = [];
    for (let k = 0; k < cap; k++) {
      const id = `W${o}-${k}`;
      workstationIds.push(id);
      seatPos.set(id, { col: o * 100 + (k % 10), row: Math.floor(k / 10) });
    }
    for (let k = 0; k < tables[o]; k++) {
      const id = `T${o}-${k}`;
      tableIds.push(id);
      seatPos.set(id, { col: o * 100 + 25 + (k % 10), row: Math.floor(k / 10) });
    }
    for (let k = 0; k < chairs[o]; k++) {
      const id = `C${o}-${k}`;
      chairIds.push(id);
      seatPos.set(id, { col: o * 100 + 50 + (k % 10), row: Math.floor(k / 10) });
    }
    return { workstationIds, tableIds, chairIds };
  });
  return { offices, seatPos };
}

interface TeamSpec {
  team: string;
  size: number;
  /** a lead exists (the first member) */
  lead?: boolean;
}
/** Characters for the teams, ids from 1, in the order given (the entitlement order = the placement order unless a test reorders). */
function people(teams: TeamSpec[], firstId = 1): AllocCharacter[] {
  const out: AllocCharacter[] = [];
  let id = firstId;
  for (const t of teams)
    for (let k = 0; k < t.size; k++)
      out.push({ id: id++, key: `${t.team}/${String(k).padStart(3, '0')}`, teamKey: t.team, isLead: t.lead !== false && k === 0 });
  return out;
}

function run(
  w: ReturnType<typeof world>,
  characters: AllocCharacter[],
  extra: Partial<Pick<AllocInput, 'held' | 'preferred' | 'placementOrder' | 'entitlement'>> = {},
) {
  return allocateDiSeats({
    offices: w.offices,
    seatPos: w.seatPos,
    entitlement: extra.entitlement ?? characters,
    placementOrder: extra.placementOrder ?? characters.map((c) => c.id),
    held: extra.held ?? new Map(),
    preferred: extra.preferred ?? new Map(),
  });
}
const perOffice = (r: ReturnType<typeof run>, n: number) => {
  const counts = new Array(n).fill(0) as number[];
  for (const o of r.officeOf.values()) counts[o]++;
  return counts;
};
const BIG = [200, 200, 200];

function assertUniqueSeats(r: ReturnType<typeof run>) {
  const seats = [...r.seatFor.values()];
  assert.equal(new Set(seats).size, seats.length, 'one character per seat, no duplicates');
}

// ── the worked cases, step by step ───────────────────────────────────────────────────────────────────────

test('worked cases N = 1..4 (one Mission): 1/0/0, 1/1/0, 1/1/1, 2/1/1 — each step follows the rule, all on workstations', () => {
  const expected: Record<number, number[]> = { 1: [1, 0, 0], 2: [1, 1, 0], 3: [1, 1, 1], 4: [2, 1, 1] };
  for (const n of [1, 2, 3, 4]) {
    const w = world(BIG);
    const chars = people([{ team: 'M', size: n }]);
    const r = run(w, chars);
    assert.deepEqual(perOffice(r, 3), expected[n], `N=${n}`);
    assert.ok([...r.kindOf.values()].every((k) => k === 'workstation'));
    assert.equal(r.fallback.size, 0);
    assertUniqueSeats(r);
  }
  // N=4 step by step: A1 Palace (largest remaining 2), A2 Palace (team office under quota), A3 Atrium (Palace full; Atrium/Observatory tie -> order), A4 Observatory
  const r = run(world(BIG), people([{ team: 'M', size: 4 }]));
  assert.deepEqual([1, 2, 3, 4].map((id) => r.officeOf.get(id)), [0, 0, 1, 2]);
  assert.deepEqual(r.quotas, [2, 1, 1]);
});

test('worked case N = 7 (one 5-member team and two singles): 3/2/2 — the big team takes Palace 3 then Atrium 2; the singles fill the Observatory', () => {
  const chars = people([
    { team: 'B', size: 5 },
    { team: 'S1', size: 1 },
    { team: 'S2', size: 1 },
  ]);
  const r = run(world(BIG), chars);
  assert.deepEqual(r.quotas, [3, 2, 2]);
  assert.deepEqual(chars.map((c) => r.officeOf.get(c.id)), [0, 0, 0, 1, 1, 2, 2]);
  assert.deepEqual(perOffice(r, 3), [3, 2, 2]);
  assertUniqueSeats(r);
});

test('worked case: capacity-capped (workstations 2/10/10, N = 9, one team) is 2/4/3 with the placement the design derives', () => {
  const chars = people([{ team: 'M', size: 9 }]);
  const r = run(world([2, 10, 10]), chars);
  assert.deepEqual(r.quotas, [2, 4, 3]);
  // A1 Atrium (largest remaining), A2-A4 Atrium, A5-A7 Observatory, A8-A9 Palace
  assert.deepEqual(chars.map((c) => r.officeOf.get(c.id)), [1, 1, 1, 1, 2, 2, 2, 0, 0]);
  assert.deepEqual(perOffice(r, 3), [2, 4, 3]);
});

test('worked case: N above capacity (workstations 2/2/2, 8 characters) seats the first six by the same rule and leaves the last two deskless', () => {
  const chars = people([{ team: 'M', size: 8 }]);
  const r = run(world([2, 2, 2]), chars);
  assert.deepEqual(chars.slice(0, 6).map((c) => r.officeOf.get(c.id)), [0, 0, 1, 1, 2, 2]);
  assert.deepEqual([...r.deskless].sort(), [7, 8]);
  assert.equal(r.seatFor.size, 6);
  assertUniqueSeats(r);
});

test('fewer characters than offices leaves offices empty: nobody is cloned or invented', () => {
  const r = run(world(BIG), people([{ team: 'M', size: 2 }]));
  assert.deepEqual(perOffice(r, 3), [1, 1, 0]);
  assert.equal(r.seatFor.size, 2);
});

// ── workstation-first ────────────────────────────────────────────────────────────────────────────────────

test('workstation-first: a lounge chair that is nearer the anchor than any free workstation is never taken', () => {
  const w = world([6, 6, 6], [3, 0, 0]);
  // put a Palace lounge chair right at the Palace workstation centroid
  const ws = w.offices[0].workstationIds.map((id) => w.seatPos.get(id)!);
  const centre = { col: Math.round(ws.reduce((a, p) => a + p.col, 0) / ws.length), row: Math.round(ws.reduce((a, p) => a + p.row, 0) / ws.length) };
  w.seatPos.set('C0-0', centre);
  const chars = people([{ team: 'M', size: 3 }]);
  const r = run(w, chars);
  for (const c of chars) assert.equal(r.kindOf.get(c.id), 'workstation');
  assert.ok(!r.seatFor.get(1)!.startsWith('C'));
  assert.equal(r.fallback.size, 0);
  // the claim is meaningful: the chair really is at least as near the anchor as the workstation that was chosen
  const dist = (id: string) => Math.abs(w.seatPos.get(id)!.col - centre.col) + Math.abs(w.seatPos.get(id)!.row - centre.row);
  assert.ok(dist('C0-0') <= dist(r.seatFor.get(1)!), 'the lounge chair was nearer, and was still not taken');
});

test('MIXED: a preserved lounge-chair preference keeps its chair and counts toward its office; the new team gets workstations by the quota rule (2/1/1)', () => {
  const w = world(BIG, [60, 0, 0]);
  const returning: AllocCharacter = { id: 1, key: 'R/000', teamKey: 'R', isLead: true };
  const team = people([{ team: 'T', size: 3 }], 2);
  const chars = [returning, ...team];
  const r = run(w, chars, { preferred: new Map([[1, ['C0-5']]]) });
  assert.equal(r.seatFor.get(1), 'C0-5', 'the preserved agent keeps the lounge chair');
  assert.equal(r.kindOf.get(1), 'chair');
  assert.ok(!r.fallback.has(1) && !r.fresh.has(1), 'preserved, not a fresh placement');
  assert.deepEqual(r.quotas, [2, 1, 1]);
  assert.deepEqual(team.map((c) => r.officeOf.get(c.id)), [0, 1, 2], 'T1 Palace (remaining 1/1/1, tie -> order), T2 Atrium, T3 Observatory');
  for (const c of team) assert.equal(r.kindOf.get(c.id), 'workstation');
  assert.deepEqual(perOffice(r, 3), [2, 1, 1]);
});

test('FALLBACK: when workstation capacity is exhausted everywhere the chair is used and labelled "chair, not workstation"', () => {
  const w = world([1, 1, 1], [2, 0, 0]);
  const chars = people([{ team: 'M', size: 4 }]);
  const r = run(w, chars);
  assert.deepEqual(r.quotas, [1, 1, 1], 'N_ws = min(4, Σ workstations) = 3');
  assert.deepEqual(chars.slice(0, 3).map((c) => r.kindOf.get(c.id)), ['workstation', 'workstation', 'workstation']);
  assert.deepEqual(chars.slice(0, 3).map((c) => r.officeOf.get(c.id)), [0, 1, 2]);
  assert.equal(r.kindOf.get(4), 'chair');
  assert.ok(r.fallback.has(4));
  assert.equal(r.officeOf.get(4), 0, 'the first office with a free chair');
  assert.ok(r.seatFor.get(4)!.startsWith('C0-'));
  assertUniqueSeats(r);
});

test('FALLBACK is only the last resort: a free workstation anywhere always wins, even in another office', () => {
  const w = world([1, 5, 0], [3, 0, 0]);
  const chars = people([{ team: 'M', size: 3 }]);
  const r = run(w, chars);
  assert.equal(r.fallback.size, 0);
  for (const c of chars) assert.equal(r.kindOf.get(c.id), 'workstation');
});

test('a seat that is not in an office (the reserved reception seat) can never be used: it is simply not offered', () => {
  const w = world([3, 3, 3]);
  const chars = people([{ team: 'M', size: 9 }]);
  const r = run(w, chars);
  const reserved = 'RESERVED-BENCH';
  assert.ok(![...r.seatFor.values()].includes(reserved));
  // and a character that claims it (a saved seat id) is ignored deterministically and placed normally
  const r2 = run(w, chars, { preferred: new Map([[1, [reserved]]]) });
  assert.ok(![...r2.seatFor.values()].includes(reserved));
  assert.equal(r2.seatFor.size, 9);
  assert.ok(r2.fresh.has(1), 'its claim was ignored, so it was placed fresh');
});

// ── two passes, no churn ─────────────────────────────────────────────────────────────────────────────────

test('TWO PASSES: with the discovery order REORDERED on reload (new agents listed before the returning ones) every returning agent gets its exact preferred seat', () => {
  const w = world(BIG, [10, 0, 0]);
  const returning: AllocCharacter[] = [
    { id: 11, key: 'old/000', teamKey: 'OLD', isLead: true },
    { id: 12, key: 'old/001', teamKey: 'OLD', isLead: false },
    { id: 13, key: 'old/002', teamKey: 'S', isLead: true },
  ];
  const fresh = people([{ team: 'NEW', size: 3 }], 1);
  // CONTROL: with no preferences the fresh character listed FIRST takes the seat nearest the office anchor — that exact seat is what the returning agent prefers
  const control = run(w, [...fresh, ...returning]);
  const anchor = control.seatFor.get(1)!;
  assert.ok(control.fresh.has(1) && anchor.startsWith('W0-'), 'control: the first fresh character takes the Palace anchor seat');
  const prefer = new Map<number, string[]>([
    [11, [anchor]],
    [12, ['W1-3']],
    [13, ['W2-7']],
  ]);
  const order = [...fresh, ...returning]; // new agents now come first
  const r = run(w, order, { preferred: prefer });
  assert.equal(r.seatFor.get(11), anchor, 'a returning agent keeps the seat a fresh earlier one would have taken');
  assert.equal(r.seatFor.get(12), 'W1-3');
  assert.equal(r.seatFor.get(13), 'W2-7');
  for (const f of fresh) {
    assert.ok(r.fresh.has(f.id));
    assert.ok(![anchor, 'W1-3', 'W2-7'].includes(r.seatFor.get(f.id)!), 'the new agents are placed around them');
  }
  assertUniqueSeats(r);
  // and the same roster in the ORIGINAL order gives every returning agent the same seat
  const r2 = run(w, [...returning, ...fresh], { preferred: prefer });
  for (const id of [11, 12, 13]) assert.equal(r2.seatFor.get(id), r.seatFor.get(id));
});

test('TWO PASSES: two characters preferring the same seat — the earlier in entitlement order gets it, the other is placed fresh', () => {
  const w = world(BIG);
  const a: AllocCharacter = { id: 1, key: 'z/000', teamKey: 'A', isLead: true };
  const b: AllocCharacter = { id: 2, key: 'a/000', teamKey: 'B', isLead: true };
  const r = run(w, [a, b], { preferred: new Map([[1, ['W0-4']], [2, ['W0-4']]]) });
  assert.equal(r.seatFor.get(1), 'W0-4', 'entitlement order decides, not the identity key');
  assert.ok(r.fresh.has(2));
  assert.notEqual(r.seatFor.get(2), 'W0-4');
  // reversed entitlement order flips the winner, deterministically
  const r2 = run(w, [b, a], { preferred: new Map([[1, ['W0-4']], [2, ['W0-4']]]) });
  assert.equal(r2.seatFor.get(2), 'W0-4');
  assert.ok(r2.fresh.has(1));
});

test('a currently HELD seat outranks another character\'s preference for it', () => {
  const w = world(BIG);
  const a: AllocCharacter = { id: 1, key: 'a/000', teamKey: 'A', isLead: true };
  const b: AllocCharacter = { id: 2, key: 'b/000', teamKey: 'B', isLead: true };
  const r = run(w, [a, b], { held: new Map([[2, 'W0-4']]), preferred: new Map([[1, ['W0-4']]]) });
  assert.equal(r.seatFor.get(2), 'W0-4');
  assert.notEqual(r.seatFor.get(1), 'W0-4');
});

test('NO CHURN: a Palace-only held roster over its quota is left exactly where it is; discovery-order permutations and joins/leaves move nobody', () => {
  const w = world(BIG);
  const team = people([{ team: 'M', size: 4 }]);
  const held = new Map(team.map((c, i) => [c.id, `W0-${i + 10}`] as const));
  const r = run(w, team, { held });
  assert.deepEqual(perOffice(r, 3), [4, 0, 0], 'over its quota of 2, and left alone');
  assert.equal(r.fresh.size, 0);
  for (const [id, seat] of held) assert.equal(r.seatFor.get(id), seat);
  // permuted discovery order
  const perm = [team[2], team[0], team[3], team[1]];
  const r2 = run(w, perm, { held });
  for (const [id, seat] of held) assert.equal(r2.seatFor.get(id), seat);
  // a new character joins (listed first): nobody who holds a seat moves
  const joiner: AllocCharacter = { id: 99, key: 'j/000', teamKey: 'J', isLead: true };
  const r3 = run(w, [joiner, ...team], { held });
  for (const [id, seat] of held) assert.equal(r3.seatFor.get(id), seat);
  assert.ok(r3.fresh.has(99));
  assert.notEqual(perOffice(r3, 3)[0], 5, 'the joiner is steered by the quota to another office');
  // a member leaves: the rest keep their seats
  const rest = team.slice(1);
  const r4 = run(w, rest, { held: new Map(rest.map((c) => [c.id, held.get(c.id)!] as const)) });
  for (const c of rest) assert.equal(r4.seatFor.get(c.id), held.get(c.id));
});

test('placement is deterministic for the same inputs and independent of object identity', () => {
  const chars = people([{ team: 'A', size: 5 }, { team: 'B', size: 3 }, { team: 'C', size: 1, lead: false }]);
  const a = run(world(BIG, [5, 0, 0]), chars);
  const b = run(world(BIG, [5, 0, 0]), chars.map((c) => ({ ...c })));
  assert.deepEqual([...a.seatFor].sort(), [...b.seatFor].sort());
});

test('a lead is the anchor: later members of its team sit near it in the same office', () => {
  const w = world([40, 40, 40]);
  const chars = people([{ team: 'M', size: 4 }]);
  const r = run(w, chars);
  const lead = w.seatPos.get(r.seatFor.get(1)!)!;
  const second = w.seatPos.get(r.seatFor.get(2)!)!; // same office (Palace holds two)
  assert.equal(r.officeOf.get(2), r.officeOf.get(1));
  assert.ok(Math.abs(lead.col - second.col) + Math.abs(lead.row - second.row) <= 2, 'adjacent workstations');
});

// ── the real size ────────────────────────────────────────────────────────────────────────────────────────

/** The bundled living metropolis after the reception reservation: tier 1 / tier 2 / tier 3 per office (measured: evidence capacity-table). */
const REAL_WS = [44, 72, 72];
const REAL_TABLES = [21, 18, 18];
const REAL_CHAIRS = [59, 0, 0];

test('n = 188 fills every workstation exactly once (44/72/72), as 188 single agents and as five teams, with no table or chair used', () => {
  const w = world(REAL_WS, REAL_CHAIRS, REAL_TABLES);
  const singles = people(Array.from({ length: 188 }, (_, i) => ({ team: `S${i}`, size: 1, lead: false })));
  const r = run(w, singles);
  assert.deepEqual(r.quotas, [44, 72, 72]);
  assert.deepEqual(perOffice(r, 3), [44, 72, 72]);
  assert.equal(new Set(r.seatFor.values()).size, 188);
  assert.ok([...r.seatFor.values()].every((s) => s.startsWith('W')), 'no table seat or chair is used while a workstation is free');
  assert.ok([...r.kindOf.values()].every((k) => k === 'workstation'));
  assert.equal(r.fallback.size, 0);
  assert.equal(r.deskless.length, 0);
  const teams = people([50, 50, 50, 28, 10].map((size, i) => ({ team: `T${i}`, size })));
  const r2 = run(w, teams);
  assert.deepEqual(perOffice(r2, 3), [44, 72, 72]);
  assert.equal(new Set(r2.seatFor.values()).size, 188);
  assert.ok([...r2.seatFor.values()].every((s) => s.startsWith('W')));
});

test('the fresh four (2/1/1) and the 39-character case (13/13/13) are placed on tier-1 workstations only, over the real capacities', () => {
  for (const [n, expected] of [[4, [2, 1, 1]], [39, [13, 13, 13]]] as const) {
    const w = world(REAL_WS, REAL_CHAIRS, REAL_TABLES);
    const r = run(w, people([{ team: 'M', size: n }]));
    assert.deepEqual(r.quotas, expected);
    assert.deepEqual(perOffice(r, 3), expected);
    assert.ok([...r.seatFor.values()].every((s) => s.startsWith('W')), `all ${n} on tier-1 workstations`);
    assert.ok([...r.kindOf.values()].every((k) => k === 'workstation'));
    assert.equal(r.fallback.size, 0);
  }
});

test('one more than the workstations: the extra character takes a TABLE seat (not a chair), labelled; the workstations stay one each', () => {
  const w = world(REAL_WS, REAL_CHAIRS, REAL_TABLES);
  const chars = people(Array.from({ length: 189 }, (_, i) => ({ team: `S${i}`, size: 1, lead: false })));
  const r = run(w, chars);
  assert.equal(r.fallback.size, 1);
  const id = [...r.fallback][0];
  assert.equal(r.kindOf.get(id), 'table');
  assert.ok(r.seatFor.get(id)!.startsWith('T'), 'a table seat, not a lounge chair');
  assert.equal(r.officeOf.get(id), 0, 'the first office with a free table seat');
  assert.equal([...r.seatFor.values()].filter((s) => s.startsWith('W')).length, 188);
  assertUniqueSeats(r);
});

test('after the tables are gone too, the next characters take chairs, labelled; nobody shares a seat; the last character is deskless', () => {
  const w = world(REAL_WS, REAL_CHAIRS, REAL_TABLES);
  const total = 188 + 57 + 59; // every tier
  const chars = people(Array.from({ length: total + 1 }, (_, i) => ({ team: `S${i}`, size: 1, lead: false })));
  const r = run(w, chars);
  const kinds = [...r.kindOf.values()];
  assert.equal(kinds.filter((k) => k === 'workstation').length, 188);
  assert.equal(kinds.filter((k) => k === 'table').length, 57);
  assert.equal(kinds.filter((k) => k === 'chair').length, 59);
  assert.equal(r.fallback.size, 57 + 59);
  assert.deepEqual(r.deskless, [total + 1]);
  assertUniqueSeats(r);
  // the order of use: no chair is taken while a table seat is free (each fresh placement is a table until 57 of them are used)
  const order = chars.map((c) => r.kindOf.get(c.id));
  const firstChair = order.indexOf('chair');
  assert.ok(firstChair >= 188 + 57, `the first chair comes after every workstation and table seat (index ${firstChair})`);
  const lastTable = order.lastIndexOf('table');
  assert.ok(lastTable < firstChair, 'tables first, then chairs');
});

// ── tiers: saved seats, labels, and the order of the fallback ────────────────────────────────────────────────

test('SAVED TABLE SEAT: a preferred table seat is kept exactly, counts toward its office, and is labelled a table — not fresh, not fallback', () => {
  const w = world(BIG, [0, 0, 0], [20, 20, 20]);
  const returning: AllocCharacter = { id: 1, key: 'R/000', teamKey: 'R', isLead: true };
  const team = people([{ team: 'T', size: 3 }], 2);
  const r = run(w, [returning, ...team], { preferred: new Map([[1, ['T1-5']]]) });
  assert.equal(r.seatFor.get(1), 'T1-5', 'the saved table seat is honoured although workstations are free');
  assert.equal(r.kindOf.get(1), 'table');
  assert.ok(!r.fresh.has(1) && !r.fallback.has(1), 'preserved, neither a fresh placement nor a fallback');
  assert.equal(r.officeOf.get(1), 1);
  assert.deepEqual(r.quotas, [2, 1, 1]);
  assert.ok(team.every((c) => r.kindOf.get(c.id) === 'workstation'), 'the new team is on workstations');
  assertUniqueSeats(r);
});

test('SAVED TABLE SEAT, HELD: a character already sitting at a table seat keeps it through a reallocation', () => {
  const w = world(BIG, [0, 0, 0], [20, 20, 20]);
  const chars = people([{ team: 'M', size: 3 }]);
  const held = new Map<number, string>([[2, 'T0-3']]);
  const r = run(w, chars, { held });
  assert.equal(r.seatFor.get(2), 'T0-3');
  assert.equal(r.kindOf.get(2), 'table');
  assert.ok(!r.fallback.has(2));
});

test('TIER ORDER: a free workstation in ANOTHER office beats a table seat in the team\'s own office, and a table beats a chair', () => {
  const w = world([0, 3, 0], [4, 0, 0], [4, 0, 0]);
  const r = run(w, people([{ team: 'M', size: 3 }]));
  assert.ok([...r.seatFor.values()].every((s) => s.startsWith('W1-')), 'all three on the only workstations, in the other office');
  assert.equal(r.fallback.size, 0);
  const r2 = run(w, people([{ team: 'M', size: 5 }]));
  const kinds = [1, 2, 3, 4, 5].map((id) => r2.kindOf.get(id));
  assert.deepEqual(kinds, ['workstation', 'workstation', 'workstation', 'table', 'table'], 'the 4th and 5th take table seats while chairs are still free');
  assert.deepEqual([...r2.fallback].sort(), [4, 5]);
});

test('TABLE FALLBACK: the team\'s own (latest placed) office first when it has a free table seat, nearest the team anchor', () => {
  // workstations 1/1/1, tables 0/2/2: the team's latest placed member (the 3rd) is in the Observatory, which has free tables -> the 4th stays there
  const w = world([1, 1, 1], [0, 0, 0], [0, 2, 2]);
  const r = run(w, people([{ team: 'M', size: 4 }]));
  assert.equal(r.kindOf.get(4), 'table');
  assert.equal(r.officeOf.get(4), 2, 'the team\'s current office has a free table seat');
  const r5 = run(w, people([{ team: 'M', size: 5 }]));
  assert.equal(r5.officeOf.get(5), 2, 'and the 5th stays with the team while that office still has one');
  assert.equal(r5.kindOf.get(5), 'table');
  assert.notEqual(r5.seatFor.get(5), r5.seatFor.get(4));
  assertUniqueSeats(r5);
});

test('TABLE FALLBACK: when the team\'s own office has no free table seat, the FIRST office in order that has one', () => {
  const w = world([1, 1, 1], [0, 0, 0], [0, 2, 0]);
  const r = run(w, people([{ team: 'M', size: 4 }]));
  assert.equal(r.kindOf.get(4), 'table');
  assert.equal(r.officeOf.get(4), 1, 'the Observatory (the team\'s office) has no table seat; the Atrium is the first office that does');
  assert.ok(r.fallback.has(4));
});

test('PRESERVED SEATS COUNT TOWARD THEIR OFFICE: an office over its share still takes a fresh agent when it alone has a free workstation (nobody is left deskless)', () => {
  // Palace: 2 workstations and 3 table seats; the other offices have no workstation. Three agents hold Palace TABLE seats, so the Palace is above its
  // quota (2) before the fourth, fresh agent is placed — and it is still placed, on a Palace workstation, because no other office has one free.
  const w = world([2, 0, 0], [0, 0, 0], [3, 0, 0]);
  const held: AllocCharacter[] = people([{ team: 'H', size: 3 }]);
  const fresh: AllocCharacter = { id: 4, key: 'F/000', teamKey: 'F', isLead: true };
  const r = run(w, [...held, fresh], { preferred: new Map([[1, ['T0-0']], [2, ['T0-1']], [3, ['T0-2']]]) });
  assert.deepEqual(r.quotas, [2, 0, 0]);
  assert.deepEqual([1, 2, 3].map((id) => r.kindOf.get(id)), ['table', 'table', 'table'], 'the three preserved agents keep their table seats');
  assert.equal(r.assigned[0], 4, 'the preserved table seats are counted toward the Palace, which is above its quota of 2');
  assert.equal(r.kindOf.get(4), 'workstation', 'the fresh agent still gets the workstation');
  assert.ok(r.seatFor.get(4)!.startsWith('W0-'));
  assert.ok(r.fresh.has(4) && !r.fallback.has(4));
  assert.equal(r.deskless.length, 0);
  // and when another office has a free workstation, the over-quota Palace is skipped in its favour
  const w2 = world([2, 2, 0], [0, 0, 0], [3, 0, 0]);
  const r2 = run(w2, [...held, fresh], { preferred: new Map([[1, ['T0-0']], [2, ['T0-1']], [3, ['T0-2']]]) });
  assert.equal(r2.officeOf.get(4), 1, 'the office under its share takes the fresh agent');
  assert.equal(r2.kindOf.get(4), 'workstation');
});

test('QUOTAS USE TIER-1 CAPACITY ONLY: table seats and chairs never raise a quota, and a character beyond Σ tier-1 is a labelled fallback, not a quota', () => {
  const w = world([2, 2, 2], [3, 0, 0], [5, 5, 5]);
  const r = run(w, people([{ team: 'M', size: 9 }]));
  assert.deepEqual(r.quotas, [2, 2, 2]);
  assert.equal([...r.kindOf.values()].filter((k) => k === 'workstation').length, 6);
  assert.equal(r.fallback.size, 3);
  assert.ok([...r.fallback].every((id) => r.kindOf.get(id) === 'table'));
});
