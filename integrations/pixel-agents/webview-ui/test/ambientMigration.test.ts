/**
 * SAVED AMBIENT STATE ACROSS THE PLAN CHANGE (office/ambient/ambientSave.ts: the exact-predecessor migration).
 *
 * The snapshots in `fixtures/ambient-snapshot.S4.*.json` were built by the UNMODIFIED S4 code (labelled baseline). The new build's plan differs
 * from S4's only in the office places (see ambientPlanGolden.test.ts), so:
 *   - a preset whose plan did NOT change (expanded-office) applies EXACTLY as today (`applied`, exact: true) — never `migrated` merely because the code is new;
 *   - a preset whose plan changed is `migrated`: unchanged residents restored exactly, changed ones fresh at their posts, counts disclosed, never an exact claim;
 *   - in transit: if no saved waiting/riding resident changed, trains and passengers are restored exactly with the transit counters untouched; if one did, the whole
 *     migration is REFUSED with a fixed reason, NOTHING is applied and the saved file is left alone (saving is withheld) — transit is never partially reset;
 *   - anything that is not the exact predecessor is `other-world`; a snapshot saved by this build round-trips exactly; the generic validators are untouched.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { MIGRATION_REFUSED_IN_TRANSIT } from '../src/office/ambient/ambientSave.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const FIX = path.join(__dirname, 'fixtures');
const OFFICE_PLACES = new Set(['Palace', 'Botanical Atrium', 'Deco Observatory']);
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);
beforeAll(() => loadBundledCatalog());

const layoutOf = (name: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds', `${name}.json`), 'utf8')) as OfficeLayout;
interface SnapshotLike {
  world: string;
  residents: Saved[];
  /** [id, dir, x, target, row, state, timer, v, passengers] */
  trains: Array<Array<string | number | string[]>>;
}
const snapshotOf = (preset: string): SnapshotLike => JSON.parse(fs.readFileSync(path.join(FIX, `ambient-snapshot.S4.${preset}.json`), 'utf8')) as SnapshotLike;
type Saved = [string, number, number, number, number, number];
interface GoldenPreset {
  fingerprint: string;
  residents: Array<[string, string, string, ...unknown[]]>;
  riders: string[];
}
const GOLDEN = JSON.parse(fs.readFileSync(path.join(FIX, 'ambient-plan-golden.S4.json'), 'utf8')) as unknown as Record<string, GoldenPreset>;
/** The fields of an ambient resident these tests read. */
interface ResidentLike {
  id: string;
  district: string;
  routine: Int32Array;
  routineIndex: number;
  tileCol: number;
  tileRow: number;
  state: string;
  timer: number;
}
const residentsOf = (o: OfficeState): ResidentLike[] => o.ambient!.residents as unknown as ResidentLike[];

function fresh(name: string): OfficeState {
  const o = new OfficeState(layoutOf(name));
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  return o;
}
const STATE_OF_CODE: Record<number, string> = { 0: 'dwell', 1: 'need-path', 2: 'waiting', 3: 'riding', 4: 'retry' };

const worldState = (o: OfficeState) =>
  JSON.stringify([
    residentsOf(o).map((r) => [r.id, r.routineIndex, r.tileCol, r.tileRow, r.state, r.timer]),
    (o.transit?.trains ?? []).map((t) => [t.id, t.x, t.state, [...t.passengers]]),
    o.transit ? { ...o.transit.totals } : null,
  ]);

test('the S4 snapshot of the living metropolis really holds residents in transit (3 waiting, 9 riding, 2 trains, 9 passengers) and none of them is an office resident', () => {
  const s = snapshotOf('living-metropolis');
  const district = new Map<string, string>(GOLDEN['living-metropolis'].residents.map((r) => [r[0], r[2]]));
  const waiting = (s.residents as Saved[]).filter((r) => r[4] === 2);
  const riding = (s.residents as Saved[]).filter((r) => r[4] === 3);
  assert.equal(waiting.length, 3);
  assert.equal(riding.length, 9);
  assert.equal(s.trains.length, 2);
  assert.equal(s.trains.reduce((n, t) => n + (t[8] as string[]).length, 0), 9);
  for (const r of [...waiting, ...riding]) assert.ok(!OFFICE_PLACES.has(district.get(r[0])!), `${r[0]} (${district.get(r[0])}) is not an office resident`);
});

test('MIGRATED: an S4-built snapshot of the living metropolis, with residents waiting and riding — unchanged residents exact, changed ones fresh, trains consistent, counts disclosed', () => {
  const snap = snapshotOf('living-metropolis');
  const o = fresh('living-metropolis');
  const totalsBefore = { ...o.transit!.totals };
  const result = o.importAmbientSnapshot(snap);
  assert.equal(result.outcome, 'migrated');
  assert.equal(result.exact, undefined, 'never an exact round-trip claim');
  assert.deepEqual(result.migration, {
    restoredExact: 118,
    resetFresh: 27,
    dropped: 3,
    trainsRestored: 2,
    reasons: { changedRoutine: 24, newResident: 3, invalidTile: 0 },
  });
  assert.equal(result.migration!.restoredExact + result.migration!.resetFresh, 145);
  assert.equal(result.residentsRestored, 118);
  assert.equal(result.waitingRestored, 3);
  assert.equal(result.aboardRestored, 9);
  assert.equal(result.trainsRestored, 2);
  report('migration_living_metropolis', result.migration);
  // exact residents: exactly the saved tile, routine index, state and timer
  const byId = new Map(residentsOf(o).map((r) => [r.id, r] as const));
  const goldenIds = new Map(GOLDEN['living-metropolis'].residents.map((r) => [r[0], r] as const));
  let exact = 0;
  let fresher = 0;
  for (const [id, ri, col, row, code, timer] of snap.residents as Saved[]) {
    const r = byId.get(id);
    if (!r) continue; // dropped
    const g = goldenIds.get(id)!;
    const unchanged = !OFFICE_PLACES.has(g[2]) && r.district === g[2]; // every non-office resident is unchanged by the measured plan diff
    if (unchanged) {
      exact++;
      assert.deepEqual([r.routineIndex, r.tileCol, r.tileRow, r.state], [ri, col, row, STATE_OF_CODE[code]], `${id} is restored exactly`);
      if (code === 0 || code === 2 || code === 4) assert.equal(r.timer, timer);
    } else {
      fresher++;
      assert.equal(r.routineIndex, 0, `${id} (changed) starts fresh: its saved routine index is never applied`);
      assert.equal(r.tileRow * o.getLayout().cols + r.tileCol, r.routine[0], `${id} starts at its post`);
      assert.equal(r.state, 'dwell');
    }
  }
  assert.equal(exact, 118);
  assert.equal(fresher, 24);
  // the three new residents (the Palace's posted gardeners, in place of the former receptionists) are not in the snapshot and start fresh at their posts
  for (const id of ['amb-palace-gardener-1', 'amb-palace-gardener-2', 'amb-palace-gardener-3']) {
    const r = byId.get(id)!;
    assert.equal(r.routineIndex, 0);
    assert.equal(r.tileRow * o.getLayout().cols + r.tileCol, r.routine[0]);
  }
  // transit: membership is consistent — every passenger is one riding resident aboard exactly one train; every riding resident is aboard; nothing fabricated
  const aboard = (o.transit!.trains as Array<{ passengers: string[] }>).flatMap((t) => t.passengers);
  const riding = residentsOf(o).filter((r) => r.state === 'riding').map((r) => r.id);
  assert.equal(new Set(aboard).size, aboard.length, 'no passenger is aboard twice');
  assert.deepEqual([...aboard].sort(), [...riding].sort(), 'the passengers ARE the riding residents: none orphaned, none duplicated');
  assert.equal(aboard.length, 9);
  assert.deepEqual(o.transit!.totals, totalsBefore, 'a restore adds no boarding, alighting, arrival or crossover');
  assert.equal(o.lastAmbientRestore, result);
});

test('the same migration through the handshake path (the snapshot arrives BEFORE the world is built), and saving is held until the attempt is recorded', () => {
  const snap = snapshotOf('living-metropolis');
  const o = new OfficeState(layoutOf('living-metropolis'));
  o.setReceptionistEnabled(true);
  o.setAmbientSnapshot(snap); // held: no world yet
  assert.equal(o.serializeAmbientForSave(1), null, 'nothing is saved before the attempt');
  o.setAmbientEnabled(true); // the world is built and the held snapshot is applied
  assert.equal(o.lastAmbientRestore!.outcome, 'migrated');
  const saved = o.serializeAmbientForSave(1);
  assert.ok(saved, 'once the attempt is recorded, saving proceeds');
  assert.notEqual(saved!.world, snap.world, 'the new build saves its OWN plan afterwards');
  assert.equal(o.ambientSaveWithheldReason, null);
});

test('PERTURBED RIDER: a waiting/riding resident whose plan changed REFUSES the whole migration — nothing applied, saving withheld, no compatibility claimed', () => {
  const snap = snapshotOf('living-metropolis');
  const o = fresh('living-metropolis');
  const rider = (snap.residents as Saved[]).find((r) => r[4] === 3)![0];
  const r = residentsOf(o).find((x) => x.id === rider)!;
  r.routine = Int32Array.from(Array.from(r.routine as Int32Array).reverse()); // the new plan routes this rider differently
  const before = worldState(o);
  const result = o.importAmbientSnapshot(snap);
  assert.equal(result.outcome, 'refused');
  assert.equal(result.reason, MIGRATION_REFUSED_IN_TRANSIT);
  assert.equal(result.reason, 'migration_unsupported:in_transit_resident_changed');
  assert.equal(result.migration, undefined);
  assert.equal(worldState(o), before, 'nothing moved: no resident, train, passenger or counter');
  assert.equal(result.residentsRestored + result.trainsRestored, 0);
  // the held-snapshot path withholds saving for the session, so the saved file is never overwritten: perturb the plan, THEN let the snapshot arrive
  const w = new OfficeState(layoutOf('living-metropolis'));
  w.setReceptionistEnabled(true);
  w.setAmbientEnabled(true);
  const rr = residentsOf(w).find((x) => x.id === rider)!;
  rr.routine = Int32Array.from(Array.from(rr.routine as Int32Array).reverse());
  w.setAmbientSnapshot(snap);
  assert.equal(w.lastAmbientRestore!.outcome, 'refused');
  assert.equal(w.lastAmbientRestore!.reason, MIGRATION_REFUSED_IN_TRANSIT);
  assert.equal(w.ambientSaveWithheldReason, MIGRATION_REFUSED_IN_TRANSIT);
  assert.equal(w.serializeAmbientForSave(1), null, 'saving is withheld: the file is left exactly as it is');
});

test('a snapshot that belongs to ANOTHER world is other-world: another layout, or the right layout with a different resident set', () => {
  const o = fresh('living-metropolis');
  const before = worldState(o);
  assert.equal(o.importAmbientSnapshot(snapshotOf('metropolis')).outcome, 'other-world', 'another layout');
  const tampered = snapshotOf('living-metropolis');
  tampered.world = tampered.world.replace(/\|a[0-9a-f]{8}\|/, '|a00000000|'); // the resident-set part of the fingerprint
  const r = o.importAmbientSnapshot(tampered);
  assert.equal(r.outcome, 'other-world', 'the layout matches but the resident set is not the predecessor\'s');
  assert.equal(r.reason, 'the snapshot belongs to a different world or resident set');
  assert.equal(worldState(o), before, 'nothing changed');
  // a different layout revision is another world as well
  const revised = snapshotOf('living-metropolis');
  revised.world = revised.world.replace('|r2|', '|r9|');
  assert.equal(o.importAmbientSnapshot(revised).outcome, 'other-world');
});

test('a snapshot saved by THIS build round-trips exactly: applied, exact: true, never migrated', () => {
  const a = fresh('living-metropolis');
  for (let i = 0; i < 600; i++) a.update(0.1);
  const snap = a.serializeAmbient(1_700_000_000_000)!;
  const b = fresh('living-metropolis');
  const result = b.importAmbientSnapshot(snap);
  assert.equal(result.outcome, 'applied');
  assert.equal(result.exact, true);
  assert.equal(result.migration, undefined);
  assert.equal(result.residentsRestored, 145);
}, 120_000);

// per preset, derived from the MEASURED diff in ambientPlanGolden.test.ts (not assumed)
const PRESET_EXPECTATION: Record<string, { outcome: 'applied' | 'migrated'; migration?: Record<string, unknown> }> = {
  'expanded-office': { outcome: 'applied' },
  metropolis: {
    outcome: 'migrated',
    migration: { restoredExact: 142, resetFresh: 3, dropped: 3, trainsRestored: 0, reasons: { changedRoutine: 0, newResident: 3, invalidTile: 0 } },
  },
  'vast-forest': {
    outcome: 'migrated',
    migration: { restoredExact: 118, resetFresh: 22, dropped: 22, trainsRestored: 0, reasons: { changedRoutine: 0, newResident: 22, invalidTile: 0 } },
  },
};
test('per preset, from the measured plan diff: an unchanged plan applies EXACTLY, a changed one migrates with its measured counts', () => {
  for (const [name, expected] of Object.entries(PRESET_EXPECTATION)) {
    const o = fresh(name);
    const result = o.importAmbientSnapshot(snapshotOf(name));
    assert.equal(result.outcome, expected.outcome, name);
    if (expected.outcome === 'applied') {
      assert.equal(result.exact, true, `${name}: exact`);
      assert.equal(result.migration, undefined);
    } else {
      assert.equal(result.exact, undefined, `${name}: no exact claim`);
      assert.deepEqual(result.migration, expected.migration, name);
    }
  }
}, 120_000);

test('a held S4 snapshot of an UNCHANGED-plan preset is applied exactly through the handshake path too', () => {
  const o = new OfficeState(layoutOf('expanded-office'));
  o.setReceptionistEnabled(true);
  o.setAmbientSnapshot(snapshotOf('expanded-office'));
  o.setAmbientEnabled(true);
  assert.equal(o.lastAmbientRestore!.outcome, 'applied');
  assert.equal(o.lastAmbientRestore!.exact, true);
  assert.ok(o.serializeAmbientForSave(1), 'saving proceeds once the attempt is recorded');
});

test('the generic validator is unchanged: a snapshot that fails the contract is refused with the contract reason, never migrated', () => {
  const o = fresh('living-metropolis');
  const bad = { v: 99, world: 'x', savedAt: 1, residents: [], trains: [] };
  const r = o.importAmbientSnapshot(bad);
  assert.equal(r.outcome, 'refused');
  assert.ok(typeof r.reason === 'string' && r.reason.length > 0);
  // a predecessor snapshot with a resident missing is not a valid S4 snapshot: refused by the strict previous-plan check, not migrated
  const short = snapshotOf('living-metropolis');
  short.residents = short.residents.slice(1);
  const r2 = o.importAmbientSnapshot(short);
  assert.notEqual(r2.outcome, 'migrated');
  assert.notEqual(r2.outcome, 'applied');
});
