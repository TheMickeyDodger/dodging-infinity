/**
 * THE AMBIENT PLAN vs THE PREVIOUS BUILD (S4), per shipped preset — the measurement the saved-state migration rests on, asserted.
 *
 * `fixtures/ambient-plan-golden.S4.json` is the plan (every resident's id, job, district, routine and ride plan, the riders/journeys and the
 * fingerprint) of all four shipped presets, captured from the UNMODIFIED S4 ambient code before any P2-B/P2-C change (SYNTHETIC BASELINE, labelled).
 *   1. With the DI office off (no post, no offices) the builder's output is byte-identical to that golden: the predecessor derivation is the same builder.
 *   2. With it on, what differs is EXACTLY the measured set: the receptionist slots of the Palace and the three offices' own residents — never a resident
 *      of any other place (city, forest, retail, subway). The expected numbers below come from `evidence/p2bc/measure-plan-diff-1.log`.
 *   3. Places are classified by their EXACT name against the measured office set {Palace, Botanical Atrium, Deco Observatory}, never by substring: the
 *      "Atrium Station" / "Observatory Station" districts are ordinary places and must not be mistaken for offices.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
type Row = [string, string, string, number, number, string, string | null, number, number];
interface GoldenPreset {
  fingerprint: string;
  residents: Row[];
  riders: string[];
  journeys: Array<[string, string, number[]]>;
}
const GOLDEN = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/ambient-plan-golden.S4.json'), 'utf8')) as unknown as Record<string, GoldenPreset>;
/** The fields of the ambient world these tests read. */
interface AmbientLike {
  residents: Array<{
    id: string;
    job: string;
    district: string;
    palette: number;
    hueShift: number;
    timer: number;
    routine: Int32Array;
    rides?: Int8Array;
    tileCol: number;
    tileRow: number;
  }>;
  riders: string[];
  journeys: Array<{ id: string; kind: string; stations: number[] }>;
}
const ambientOf = (o: OfficeState): AmbientLike => o.ambient as unknown as AmbientLike;
const OFFICE_PLACES = new Set(['Palace', 'Botanical Atrium', 'Deco Observatory']);
const PRESETS = ['expanded-office', 'living-metropolis', 'metropolis', 'vast-forest'] as const;
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);

beforeAll(() => loadBundledCatalog());
const layoutOf = (name: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds', `${name}.json`), 'utf8')) as OfficeLayout;

function build(name: string, diOffice: boolean) {
  const o = new OfficeState(layoutOf(name));
  if (diOffice) o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  return o;
}
const rowsOf = (o: OfficeState): Row[] =>
  ambientOf(o).residents.map((r) => [
    r.id,
    r.job,
    r.district,
    r.palette,
    r.hueShift,
    (Array.from(r.routine) as number[]).join(','),
    r.rides ? (Array.from(r.rides) as number[]).join(',') : null,
    r.tileCol,
    r.tileRow,
  ]);
const planKey = (r: Row) => [r[1], r[2], r[3], r[4], r[5], r[6]].join('|');

test('the golden is what it says: the S4 plan of the four shipped presets, with the exact office set', () => {
  assert.match(String(GOLDEN.label), /unmodified S4/);
  for (const p of PRESETS) assert.ok(GOLDEN[p].residents.length >= 140, p);
  const places = new Set<string>(GOLDEN['living-metropolis'].residents.map((r: Row) => r[2]));
  for (const office of OFFICE_PLACES) assert.ok(places.has(office), `${office} is a place of the living metropolis`);
  // the substring trap: these stations are NOT offices, whatever their names contain
  assert.ok(places.has('Atrium Station') && places.has('Observatory Station'));
  assert.ok(!OFFICE_PLACES.has('Atrium Station') && !OFFICE_PLACES.has('Observatory Station'));
});

test('with the DI office off the builder reproduces the S4 plan EXACTLY on every shipped preset (the predecessor derivation is this same builder)', () => {
  for (const name of PRESETS) {
    const o = build(name, false);
    const g = GOLDEN[name];
    assert.equal(o.serializeAmbient(1_700_000_000_000)!.world, g.fingerprint, `${name}: the fingerprint`);
    assert.deepEqual(rowsOf(o), g.residents, `${name}: every resident's id, job, district, look, routine, rides and start tile`);
    assert.deepEqual([...ambientOf(o).riders].sort(), g.riders, `${name}: the riders`);
    assert.deepEqual(
      ambientOf(o)
        .journeys.map((j) => [j.id, j.kind, [...j.stations]])
        .sort((a, b) => (a[0] < b[0] ? -1 : 1)),
      g.journeys,
      `${name}: the journey plan`,
    );
  }
}, 120_000);

/** What the measurement found (evidence/p2bc/measure-plan-diff-1.log), per preset: totals and, for the places that differ, their own counts. */
const MEASURED: Record<string, { equal: boolean; total: Record<string, number>; places: Record<string, Record<string, number>> }> = {
  'expanded-office': { equal: true, total: { same: 140, changed: 0, gone: 0, added: 0 }, places: {} },
  'living-metropolis': {
    equal: false,
    total: { same: 118, changed: 24, gone: 3, added: 3 },
    places: {
      Palace: { same: 0, changed: 8, gone: 3, added: 3 },
      'Botanical Atrium': { same: 0, changed: 8, gone: 0, added: 0 },
      'Deco Observatory': { same: 0, changed: 8, gone: 0, added: 0 },
    },
  },
  metropolis: { equal: false, total: { same: 142, changed: 0, gone: 3, added: 3 }, places: { Palace: { same: 11, changed: 0, gone: 3, added: 3 } } },
  'vast-forest': { equal: false, total: { same: 118, changed: 0, gone: 22, added: 22 }, places: { Palace: { same: 86, changed: 0, gone: 22, added: 22 } } },
};

test('the new plan differs from S4 EXACTLY as measured, per preset — and only ever inside the exact office places', () => {
  for (const name of PRESETS) {
    const o = build(name, true);
    const g = GOLDEN[name];
    const old = new Map<string, Row>((g.residents as Row[]).map((r) => [r[0], r]));
    const now = new Map<string, Row>(rowsOf(o).map((r) => [r[0], r]));
    const byPlace: Record<string, Record<string, number>> = {};
    const bump = (place: string, k: string) => {
      byPlace[place] ??= { same: 0, changed: 0, gone: 0, added: 0 };
      byPlace[place][k]++;
    };
    for (const [id, r] of old) {
      const n = now.get(id);
      if (!n) bump(r[2], 'gone');
      else bump(r[2], planKey(r) === planKey(n) ? 'same' : 'changed');
    }
    for (const [id, n] of now) if (!old.has(id)) bump(n[2], 'added');
    const total: Record<string, number> = { same: 0, changed: 0, gone: 0, added: 0 };
    for (const v of Object.values(byPlace)) for (const k of Object.keys(total)) total[k] += v[k];
    const expected = MEASURED[name];
    assert.deepEqual(total, expected.total, `${name}: the total difference`);
    const differing = Object.fromEntries(Object.entries(byPlace).filter(([, v]) => v.changed + v.gone + v.added > 0));
    assert.deepEqual(differing, expected.places, `${name}: the places that differ, with their counts`);
    for (const place of Object.keys(differing)) assert.ok(OFFICE_PLACES.has(place), `${name}: ${place} is an office place — no other place may differ`);
    // equal plan <=> equal fingerprint: a preset whose plan did not change is applied EXACTLY (no migration), one that changed is not
    assert.equal(o.serializeAmbient(1_700_000_000_000)!.world === g.fingerprint, expected.equal, `${name}: fingerprint equality`);
    // the resident COUNT of every place is unchanged
    const counts = (rows: Row[]) => {
      const c: Record<string, number> = {};
      for (const r of rows) c[r[2]] = (c[r[2]] ?? 0) + 1;
      return c;
    };
    assert.deepEqual(counts(rowsOf(o)), counts(g.residents), `${name}: per-place resident counts`);
    report(`plan_diff_${name}`, { total, differing, fingerprintEqual: expected.equal });
  }
}, 120_000);

test('remote life is unchanged: every resident of every non-office place keeps id, job, look, INITIAL TIMER, routine, ride plan and start tile (living metropolis)', () => {
  const o = build('living-metropolis', true);
  const s4 = new Map(ambientOf(build('living-metropolis', false)).residents.map((r) => [r.id, r] as const)); // the S4 code path (DI office off), golden-pinned above
  const nowById = new Map(ambientOf(o).residents.map((r) => [r.id, r] as const));
  const now = new Map(rowsOf(o).map((r) => [r[0], r] as const));
  const remote = (GOLDEN['living-metropolis'].residents as Row[]).filter((r) => !OFFICE_PLACES.has(r[2]));
  assert.equal(remote.length, 118);
  for (const r of remote) {
    assert.deepEqual(now.get(r[0]), r, `${r[0]} is byte-stable`);
    assert.equal(nowById.get(r[0])!.timer, s4.get(r[0])!.timer, `${r[0]}: initial timer equals S4's`);
  }
  report('remote_residents_exactly_S4', { residents: remote.length, fields: 'id, job, district, palette, hue, routine, rides, start tile, initial timer' });
  const g = GOLDEN['living-metropolis'];
  assert.deepEqual([...ambientOf(o).riders].sort(), g.riders, 'the riders');
  assert.deepEqual(
    ambientOf(o)
      .journeys.map((j) => [j.id, j.kind, [...j.stations]])
      .sort((a, b) => (a[0] < b[0] ? -1 : 1)),
    g.journeys,
    'the journey plan',
  );
}, 120_000);

test('no office resident rides (by EXACT place, not substring); the four Station residents the substring rule would mistake for offices are ordinary riders', () => {
  const o = build('living-metropolis', true);
  const rows = rowsOf(o);
  const officeRiders = rows.filter((r) => OFFICE_PLACES.has(r[2]) && r[6] !== null);
  assert.deepEqual(officeRiders, [], 'no resident of Palace / Botanical Atrium / Deco Observatory has a ride plan');
  const stationRiders = rows.filter((r) => /Station$/.test(r[2]) && r[6] !== null).map((r) => r[0]);
  for (const id of ['amb-atrium-station-gardener-1', 'amb-atrium-station-receptionist-1', 'amb-observatory-station-office-visitor-1', 'amb-observatory-station-receptionist-1'])
    assert.ok(stationRiders.includes(id), `${id} is a Station rider, not an office resident`);
});

test('B3: the Palace has no ambient receptionist where the post exists (its former slots are posted gardeners); the Atrium and Observatory keep theirs; a world without the post keeps the old list', () => {
  const o = build('living-metropolis', true);
  const rows = rowsOf(o);
  assert.equal(rows.filter((r) => r[2] === 'Palace' && r[1] === 'receptionist').length, 0);
  assert.deepEqual(
    rows.filter((r) => r[2] === 'Palace').map((r) => r[1]).sort(),
    ['courier', 'courier', 'custodian', 'custodian', 'gardener', 'gardener', 'gardener', 'office-visitor', 'office-visitor', 'office-visitor', 'office-visitor'],
    'the receptionist entry is replaced IN PLACE by the existing POSTED gardener role: 11 residents, 3 of them gardeners',
  );
  assert.equal(rows.filter((r) => r[2] === 'Botanical Atrium' && r[1] === 'receptionist').length, 2);
  assert.equal(rows.filter((r) => r[2] === 'Deco Observatory' && r[1] === 'receptionist').length, 2);
  const withoutPost: OfficeLayout = { ...layoutOf('living-metropolis'), furniture: layoutOf('living-metropolis').furniture.filter((f) => f.uid !== 'expansion-148') };
  const p = new OfficeState(withoutPost);
  p.setReceptionistEnabled(true);
  p.setAmbientEnabled(true);
  assert.equal(rowsOf(p).filter((r) => r[2] === 'Palace' && r[1] === 'receptionist').length, 3, 'no post, no change: the Palace keeps its three ambient receptionists');
}, 120_000);
