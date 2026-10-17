/**
 * AMBIENT PERSISTENCE (M3 S4, snapshot v2: ACTIVE JOURNEYS ARE DURABLE) on the SHIPPED living
 * metropolis. A same-world snapshot restores residents (standing, waiting, ABOARD) and trains
 * (running, dwelling, crossing over, terminus dwell, onboard membership) from the saved point, and
 * journeys CONTINUE: restored riders alight only when their train actually reaches their destination
 * after the runtime resumes. A restore FABRICATES NOTHING (no events, no counters; a later real
 * alighting of a restored rider is flagged `restored`), closed time never advances anything, nothing
 * is duplicated, and an invalid or incompatible snapshot is REFUSED WHOLE with a reason, never
 * quietly relocated. Walking residents resume by re-planning (a bounded, reconstructible reset).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { AmbientSaveState } from '../../core/src/ambientSave.ts';
import { AMBIENT_SAVE_MAX_BYTES, ambientSaveBytes, checkAmbientSave } from '../../core/src/ambientSave.ts';
import { AMBIENT_CATCHUP_MAX_SEC } from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const living = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));
const r4 = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'));
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v));

let source: OfficeState;
let saved: AmbientSaveState;
/** Snapshots taken at moments that exercise each train state, from ONE deterministic run. */
const byTrainState: Partial<Record<number, AmbientSaveState>> = {};
/** The first snapshot of the same run holding a resident WAITING on a platform. */
let withWaiting: AmbientSaveState | undefined;
/** The first snapshot of the same run holding riders aboard (the main snapshot). */
let withAboard: AmbientSaveState | undefined;
const RUN_MAX_STEPS = 6000; // 600 simulated seconds: the run stops early once every case was seen

beforeAll(() => {
  loadBundledCatalog();
  source = new OfficeState(living());
  source.setAmbientEnabled(true);
  for (let k = 0; k < RUN_MAX_STEPS; k++) {
    source.update(0.1);
    const s = source.serializeAmbient(Date.now())!;
    for (const t of s.trains) if (byTrainState[t[5]] === undefined && (t[5] !== 0 || t[8].length > 0)) byTrainState[t[5]] = clone(s);
    if (!withWaiting && s.residents.some((r) => r[4] === 2)) withWaiting = clone(s);
    if (!withAboard && k >= 600 && s.residents.filter((r) => r[4] === 3).length >= 2) withAboard = clone(s);
    if (withWaiting && withAboard && [0, 1, 2, 3].every((c) => byTrainState[c])) break;
  }
  // a precondition, asserted in the first test: an undefined here fails, it never skips
  if (withAboard) saved = withAboard;
}, 600_000);

/** Mutation: a running train with a rider aboard, moved so that rider's destination is BEHIND it
 *  (same track, same direction, next station one past the destination). The rider and station are
 *  ASSERTED to exist; nothing is skipped. */
function moveTrainPastRider(s: AmbientSaveState): void {
  const ref = restoreInto(saved);
  const stations = ref.transit!.line.stations;
  let done = false;
  for (const t of s.trains) {
    for (const id of t[8]) {
      const d = ref.ambient!.destinationOf(id);
      const past = d + t[1];
      if (done || d < 0 || past < 0 || past >= stations.length) continue;
      t[5] = 0; // running
      t[3] = past;
      t[2] = (stations[d].c + stations[past].c) / 2;
      done = true;
    }
  }
  assert.ok(done, 'a rider whose destination can be put behind its train exists to cover');
}

const restoreInto = (snapshot: unknown, layout = living()) => {
  const o = new OfficeState(layout);
  o.setAmbientSnapshot(clone(snapshot)); // held: arrives in the handshake before the build
  o.setAmbientEnabled(true);
  return o;
};

test('PRECONDITIONS (asserted, never skipped): the run produced riders aboard, a waiting rider, and every train state', () => {
  assert.ok(withAboard, 'the simulation produced a snapshot with riders aboard to cover');
  assert.ok(withWaiting, 'the simulation produced a waiting rider to cover');
  for (const code of [0, 1, 2, 3]) assert.ok(byTrainState[code], `the simulation produced train state ${code} to cover`);
});

test('the saved snapshot is admissible, holds active journeys, and fits its byte ceiling', () => {
  assert.equal(checkAmbientSave(saved), null);
  assert.ok(saved.residents.some((r) => r[4] === 3), 'riders aboard at save');
  assert.ok(ambientSaveBytes(saved) <= AMBIENT_SAVE_MAX_BYTES, `${ambientSaveBytes(saved)} bytes`);
});

test('load: the state restores from the saved point — residents (standing, walking, waiting, aboard, backing off) and trains with onboard membership', () => {
  const o = restoreInto(saved);
  assert.equal(o.lastAmbientRestore?.outcome, 'applied', o.lastAmbientRestore?.reason ?? '');
  assert.equal(o.lastAmbientRestore?.aboardRestored, saved.residents.filter((r) => r[4] === 3).length);
  assert.equal(o.lastAmbientRestore?.exact, true, 'the restore self-check agrees');
  const again = o.serializeAmbient(saved.savedAt)!;
  assert.deepEqual(again.residents, saved.residents);
  assert.deepEqual(again.trains, saved.trains);
});

test('LOAD FABRICATES NOTHING: no event and no counter at restore', () => {
  const o = restoreInto(saved);
  const t = o.transit!.snapshot();
  assert.deepEqual(o.lastAmbientRestore?.totalsAfter, { boardings: 0, alightings: 0, arrivals: 0, crossovers: 0 });
  assert.equal(t.events.length, 0);
  assert.deepEqual(t.totals, { boardings: 0, alightings: 0, arrivals: 0, crossovers: 0 });
});

test('journeys CONTINUE after the runtime resumes: each restored rider alights at its own destination, flagged restored, without a boarding in this runtime', () => {
  const o = restoreInto(saved);
  const riders = saved.trains.flatMap((t) => t[8]);
  assert.ok(riders.length > 0);
  const dest = new Map(riders.map((id) => [id, o.ambient!.destinationOf(id)]));
  for (let k = 0; k < 3000 && o.transit!.events.filter((e) => e.kind === 'alight' && e.restored).length < riders.length; k++) o.update(0.1);
  const events = o.transit!.events;
  for (const id of riders) {
    const alight = events.find((e) => e.kind === 'alight' && e.resident === id);
    assert.ok(alight, `${id} alighted`);
    assert.equal(alight.restored, true, `${id}: flagged as restored aboard`);
    assert.equal(alight.station, o.transit!.line.stations[dest.get(id)!].name, `${id}: at its own destination`);
    assert.ok(!events.some((e) => e.kind === 'board' && e.resident === id && e.t < alight.t), `${id}: no boarding fabricated before it`);
  }
});

test('NO DUPLICATES: one of each resident; a rider aboard is in the resident set once and on exactly one train', () => {
  const o = restoreInto(saved);
  const ids = o.getAmbientResidents().map((r) => r.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(ids.length, source.getAmbientResidents().length);
  const aboard = o.getTrains().flatMap((t) => t.passengers);
  assert.equal(new Set(aboard).size, aboard.length);
  for (const id of aboard) assert.equal(o.getAmbientResidents().find((r) => r.id === id)?.state, 'riding');
  for (const r of o.getAmbientResidents().filter((x) => x.state === 'riding')) assert.equal(o.ambient!.residentAt(r.x, r.y)?.id === r.id, false, 'aboard: not on the ground');
});

test('dwell, crossover and terminus-dwell trains, and waiting riders, restore in their state and resume from it', () => {
  for (const code of [1, 2, 3]) {
    const snap = byTrainState[code];
    assert.ok(snap, `train state ${code} was produced to cover`);
    const o = restoreInto(snap);
    assert.equal(o.lastAmbientRestore?.outcome, 'applied', `${code}: ${o.lastAmbientRestore?.reason ?? ''}`);
    const savedTrain = snap.trains.find((t) => t[5] === code)!;
    const tr = o.getTrains().find((t) => t.id === savedTrain[0])!;
    assert.equal(tr.state, ['run', 'dwell', 'crossover', 'dwell-turn'][code]);
    assert.equal(tr.timer, savedTrain[6]);
    for (let k = 0; k < 120; k++) o.update(0.1);
    assert.ok(o.transit!.events.length > 0, `${code}: the restored train carries on`);
  }
  assert.ok(withWaiting, 'the simulation produced a waiting rider to cover');
  const o = restoreInto(withWaiting);
  assert.equal(o.lastAmbientRestore?.outcome, 'applied', o.lastAmbientRestore?.reason ?? '');
  const waiting = withWaiting.residents.filter((r) => r[4] === 2);
  assert.ok(waiting.length > 0);
  assert.equal(o.lastAmbientRestore?.waitingRestored, waiting.length);
  for (const w of waiting) {
    const r = o.getAmbientResidents().find((x) => x.id === w[0]);
    assert.equal(r?.state, 'waiting', `${w[0]} still waiting`);
    assert.equal(r?.timer, w[5], `${w[0]}: its remaining wait`);
    assert.deepEqual([r?.tileCol, r?.tileRow], [w[2], w[3]], `${w[0]}: on its platform tile`);
  }
});

test('CLOSED TIME NEVER ADVANCES A JOURNEY: a snapshot saved decades ago restores exactly the saved point', () => {
  const old = clone(saved);
  old.savedAt = 0;
  const o = restoreInto(old);
  assert.deepEqual(o.serializeAmbient(0)!.trains, saved.trains);
});

test('REFUSED WHOLE, with a reason, and nothing changes: other world, contract, resident set, ride and train inconsistencies', () => {
  const cases: Array<[string, (s: AmbientSaveState) => void, string]> = [
    ['another version', (s) => ((s as unknown as Record<string, unknown>).v = 1), 'version'],
    ['a resident missing', (s) => s.residents.pop(), 'residents'],
    ['a rider aboard no train', (s) => s.trains.forEach((t) => (t[8] = [])), 'aboard no train'],
    ['a train on the wrong track', (s) => (s.trains[0][4] = s.trains[0][4] + 2), 'track'],
    ['a dwelling train away from its station', (s) => { s.trains[0][5] = 1; s.trains[0][2] = s.trains[0][2] + 3.5; }, 'away from its station'],
    ['a rider heading behind its train', (s) => moveTrainPastRider(s), 'behind the train'],
  ];
  assert.ok(cases.every(([, , hint]) => hint.length > 0), 'every case names the reason it must give');
  for (const [name, mutate, hint] of cases) {
    const s = clone(saved);
    mutate(s);
    const o = new OfficeState(living());
    o.setAmbientEnabled(true);
    const before = o.serializeAmbient(0);
    o.setAmbientSnapshot(s);
    assert.notEqual(o.lastAmbientRestore?.outcome, 'applied', name);
    assert.ok(o.lastAmbientRestore?.reason, `${name}: a stated reason`);
    assert.match(o.lastAmbientRestore!.reason!, new RegExp(hint), name);
    assert.deepEqual(o.serializeAmbient(0), before, `${name}: nothing changed`);
  }
  const other = new OfficeState(r4());
  other.setAmbientEnabled(true);
  other.setAmbientSnapshot(clone(saved));
  assert.equal(other.lastAmbientRestore?.outcome, 'other-world');
  assert.ok(other.lastAmbientRestore?.reason);
});

test('hidden-view catch-up stays bounded', () => {
  const o = restoreInto(saved);
  const advanced = o.catchUpAmbient(3600, 0);
  assert.ok(advanced <= AMBIENT_CATCHUP_MAX_SEC + 1e-9 && advanced > 0);
});
