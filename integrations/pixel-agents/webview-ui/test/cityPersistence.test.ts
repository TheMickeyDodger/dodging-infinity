/**
 * ITEM 18 (W1): SAVING AND RESTORING THE CITY. A saved record (contract v2) is applied by `CityEffects.loadRecord`; the season and every
 * region's weather then CONTINUE from the time left they were saved with. Visible running time only: nothing is caught up for the time the
 * view was closed, nothing overdue is replayed, and a clock that was held (Manual, paused) stays held. `CitySaveGate` / `CitySaver` decide WHEN a save
 * is due, at a bounded rate and never per frame. The browser round trip through the real App is in livingCityApp.test.ts.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test, vi } from 'vitest';

import * as core from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import { CityEffects } from '../src/office/city/cityEffects.ts';
import { CitySaveGate, CitySaver, startCitySaving } from '../src/office/city/cityPersist.ts';
import { drawWeather, RegionalWeather } from '../src/office/city/cityRegionWeather.ts';
import { SeasonEngine } from '../src/office/city/citySeason.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const SEED = 0x51a7;
const classes = Uint8Array.from([0, 1, 2, 3, 4]);
const record = (over: Partial<core.CityPresentationV2> = {}): core.CityPresentationV2 => ({
  ...structuredClone(core.defaultCityPresentationV2()),
  ...over,
});
/** Run `ms` of visible running time through effects in the loop's own 0.25 s steps. */
const run = (e: CityEffects, ms: number, reduced = false) => {
  for (let t = 0; t < ms; t += 250) e.update(0.25, 0, reduced);
};
const valid = (r: core.CityPresentationV2) => assert.equal(core.checkCityPresentationV2(r), null, JSON.stringify(r).slice(0, 100));

test('THE SAVED SAVE-RATE BOUNDS are exactly these values', () => {
  assert.equal(C.CITY_SAVE_POLL_MS, 1_000);
  assert.equal(C.CITY_SAVE_MIN_GAP_MS, 2_000);
  assert.equal(C.CITY_SAVE_CHECKPOINT_MS, 60_000);
  assert.equal(C.CITY_WEATHER_STEP_LIMIT, 1_000_000);
});

test('ROUND TRIP: what the city records, a new city loads and records back identically', () => {
  const a = new CityEffects();
  a.setPresentation({ v: 1, lighting: 'night', weather: 'off', traffic: 'low' }, 0, true);
  a.chooseSeason('winter', true);
  a.chooseWeather('heavy-snow', true);
  run(a, 20_000);
  const saved = a.record();
  valid(saved);
  assert.equal(saved.lighting, 'night');
  assert.equal(saved.traffic, 'low');
  assert.deepEqual(saved.season, { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 });
  assert.equal(saved.weather.mode, 'heavy-snow');
  const b = new CityEffects();
  b.loadRecord(JSON.parse(JSON.stringify(saved)), 0, true);
  assert.deepEqual(b.record(), saved);
  // and an Automatic city mid-schedule
  const c = new CityEffects();
  run(c, 95_000);
  const mid = c.record();
  valid(mid);
  const d = new CityEffects();
  d.loadRecord(JSON.parse(JSON.stringify(mid)), 0, false);
  assert.deepEqual(d.record(), mid);
});

test('THE SEASON CONTINUES from the time left it was saved with; nothing is caught up and nothing is replayed', () => {
  const e = new CityEffects();
  e.loadRecord(record({ season: { mode: 'auto', season: 'autumn', paused: false, remaining: 600_000 } }), 0, false);
  assert.equal(e.seasons.season, 'autumn');
  assert.equal(e.seasons.remainingMs, 600_000);
  assert.equal(e.seasons.frame()?.from ?? null, null, 'no cross-fade on load');
  run(e, 10_000);
  assert.equal(e.seasons.remainingMs, 590_000);
  // a huge gap in the loop is clipped: closing the view for hours changes nothing, because nothing runs while it is closed
  const before = JSON.stringify(e.record());
  assert.equal(JSON.stringify(e.record()), before);
  e.update(7200, 0, false); // clipped to one 0.25 s step
  assert.equal(e.seasons.remainingMs, 589_750);
  // saved with five seconds left: the season turns after five seconds of RUNNING time, once, into a fresh full interval
  const f = new CityEffects();
  f.loadRecord(record({ season: { mode: 'auto', season: 'autumn', paused: false, remaining: 5_000 } }), 0, true);
  run(f, 4_750);
  assert.equal(f.seasons.season, 'autumn');
  run(f, 250);
  assert.equal(f.seasons.season, 'winter');
  assert.equal(f.seasons.remainingMs, 1_800_000);
});

test('A PAUSED season stays paused with its time left, and a HELD (Manual) season stays held for hours', () => {
  const paused = new CityEffects();
  paused.loadRecord(record({ season: { mode: 'auto', season: 'summer', paused: true, remaining: 123_000 } }), 0, true);
  run(paused, 3 * 3_600_000);
  assert.deepEqual(paused.seasons.persist(), { mode: 'auto', season: 'summer', paused: true, remaining: 123_000 });
  paused.resumeSeasons();
  run(paused, 3_000);
  assert.equal(paused.seasons.remainingMs, 120_000);
  const held = new CityEffects();
  held.loadRecord(record({ season: { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 } }), 0, true);
  run(held, 4 * 1_800_000);
  assert.equal(held.seasons.season, 'winter');
  assert.equal(held.seasons.mode, 'manual');
});

test('EACH REGION continues its own schedule: seed, draw index, wet streak and time left decide the next draw exactly', () => {
  const regions = [
    // every saved kind is one its own region may show in Winter (City: clear or light snow; Coast: also rain; Woodland and Highlands: also
    // heavy snow; Farmland: clear or light snow), so nothing is repaired at the restore and the continuation is the saved state exactly
    { kind: 'light-snow' as const, step: 7, wet: 1, remaining: 1_000 },
    { kind: 'clear' as const, step: 40, wet: 0, remaining: 100_000 },
    { kind: 'light-snow' as const, step: 2, wet: 2, remaining: 750 },
    { kind: 'clear' as const, step: 0, wet: 0, remaining: 299_000 },
    { kind: 'light-snow' as const, step: 11, wet: 1, remaining: 250 },
  ];
  const seed = 987_654;
  const w = new RegionalWeather(SEED, 'winter', classes);
  w.restore({ mode: 'auto', seed, regions }, 'winter');
  assert.equal(w.scheduleSeed, seed);
  assert.deepEqual([0, 1, 2, 3, 4].map((k) => w.slotState(k).step), [7, 40, 2, 0, 11]);
  for (let t = 0; t < 1_500; t += 250) w.update(0.25, true);
  // slots 0, 2 and 4 drew once (their time ran out), slots 1 and 3 did not; every draw is the pure function of the saved state
  for (const slot of [0, 2, 4]) {
    const saved = regions[slot];
    const expected = drawWeather(seed, slot, saved.step, 'winter', slot, saved.wet);
    const now = w.slotState(slot);
    assert.equal(now.step, saved.step + 1, `slot ${slot} drew exactly once`);
    assert.equal(now.kind, expected.kind, `slot ${slot} kind`);
    // the draw happens in the step that uses up the time left; the steps after it count down the new interval
    const drawnAt = Math.ceil(saved.remaining / 250) * 250;
    assert.equal(now.remainingMs, expected.intervalMs - (1_500 - drawnAt), `slot ${slot} interval`);
    assert.equal(now.wetStreak, expected.kind === 'clear' ? 0 : saved.wet + 1);
  }
  assert.equal(w.slotState(1).step, 40);
  assert.equal(w.slotState(1).remainingMs, 100_000 - 1_500);
  assert.equal(w.slotState(3).remainingMs, 299_000 - 1_500);
});

test('NO OVERDUE REPLAY: a region saved with no time left draws ONCE at the first step, however long the gap in the loop', () => {
  const w = new RegionalWeather(SEED, 'summer', classes);
  w.restore(
    { mode: 'auto', seed: SEED, regions: [0, 1, 2, 3, 4].map(() => ({ kind: 'clear' as const, step: 5, wet: 0, remaining: 0 })) },
    'summer',
  );
  w.update(3600, false); // a one-hour "frame": clipped to 0.25 s
  for (let k = 0; k < 5; k++) assert.equal(w.slotState(k).step, 6, `slot ${k} drew once`);
});

test('THE DRAW INDEX WRAPS at the pinned limit, and the record stays valid', () => {
  const w = new RegionalWeather(SEED, 'summer', classes);
  w.restore(
    { mode: 'auto', seed: SEED, regions: [0, 1, 2, 3, 4].map(() => ({ kind: 'clear' as const, step: 999_999, wet: 0, remaining: 100 })) },
    'summer',
  );
  w.update(0.25, true);
  for (let k = 0; k < 5; k++) assert.equal(w.slotState(k).step, 0, `slot ${k} wrapped to 0`);
  valid({ ...core.defaultCityPresentationV2(), weather: w.persist() });
});

test('A MANUAL weather mode restores as that kind in EVERY region, even from a migrated v1 record whose regions are quiet', () => {
  const migrated = core.migrateCityPresentation({ v: 1, lighting: 'dusk', weather: 'snow', traffic: 'off' });
  assert.ok(migrated.ok);
  if (!migrated.ok) return;
  const e = new CityEffects();
  e.loadRecord(migrated.state, 0, true);
  assert.equal(e.presentation.lighting, 'dusk');
  assert.equal(e.presentation.traffic, 'off');
  assert.equal(e.regional.mode, 'light-snow');
  for (let k = 0; k < 5; k++) assert.equal(e.regional.kindAt(k), 'light-snow');
  assert.equal(e.regional.lightSnow.count, 300, 'the light-snow field is live');
  assert.deepEqual(e.seasons.persist(), { mode: 'auto', season: 'summer', paused: false, remaining: 1_800_000 });
  // Clear, Rain and Light snow all restore as manual choices
  for (const [weather, kind] of [['off', 'clear'], ['rain', 'rain'], ['snow', 'light-snow']] as const) {
    const m = core.migrateCityPresentation({ v: 1, lighting: 'day', weather, traffic: 'normal' });
    assert.ok(m.ok);
    if (!m.ok) continue;
    const x = new CityEffects();
    x.loadRecord(m.state, 0, true);
    assert.deepEqual([0, 1, 2, 3, 4].map((k) => x.regional.kindAt(k)), [kind, kind, kind, kind, kind]);
  }
});

/** A winter automatic state that every region's own rules allow: what the product itself saves. */
const REACHABLE_WINTER = [
  { kind: 'clear' as const, step: 3, wet: 0, remaining: 150_000 }, // City
  { kind: 'rain' as const, step: 4, wet: 1, remaining: 140_000 }, // Coast: a winter Coast may rain
  { kind: 'heavy-snow' as const, step: 5, wet: 2, remaining: 130_000 }, // Woodland: winter heavy snow
  { kind: 'light-snow' as const, step: 6, wet: 1, remaining: 120_000 }, // Highlands
  { kind: 'clear' as const, step: 7, wet: 0, remaining: 110_000 }, // Farmland
];
const winterRecord = (regions: typeof REACHABLE_WINTER): core.CityPresentationV2['weather'] => ({ mode: 'auto', seed: SEED, regions });
const states = (w: RegionalWeather) => [0, 1, 2, 3, 4].map((k) => { const x = w.slotState(k); return [x.kind, x.step, x.wetStreak, x.remainingMs]; });

test('(a) A VALID, REACHABLE RESTORE IS KEPT EXACTLY: every region keeps its kind, draw index, wet streak and time left, with or without the real classes known', () => {
  for (const classesFirst of [true, false]) {
    const w = new RegionalWeather(SEED, 'winter');
    if (classesFirst) w.setClasses(classes, true); // the real classes were already known when the record arrives
    else w.setClasses(Uint8Array.from([0, 0, 0, 0, 0]), false); // the placeholder window, declared as OfficeState's constructor declares it
    w.restore(winterRecord(REACHABLE_WINTER), 'winter');
    if (!classesFirst) w.setClasses(classes, true); // the real classes arrive afterwards
    assert.deepEqual(states(w), REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet, r.remaining]), `classes first: ${classesFirst}`);
    // and the same after the first running steps (only the time left moves)
    w.update(0.25, true);
    assert.deepEqual(states(w), REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet, r.remaining - 250]));
  }
});

test('(b) AN INCONSISTENT RESTORE IS REPAIRED AT ONCE AND DETERMINISTICALLY: a kind that is not eligible for its class and season is re-drawn, for every slot, even when its class did not change', () => {
  // slot 0 is a City (heavy snow is not eligible there), slot 3 a Highlands (rain is not eligible in winter), slot 4 a Farmland (heavy snow
  // is not eligible there); slots 1 and 2 are fine
  const broken = [
    { kind: 'heavy-snow' as const, step: 4, wet: 1, remaining: 150_000 },
    { kind: 'rain' as const, step: 5, wet: 1, remaining: 140_000 },
    { kind: 'light-snow' as const, step: 6, wet: 1, remaining: 130_000 },
    { kind: 'rain' as const, step: 7, wet: 1, remaining: 120_000 },
    { kind: 'heavy-snow' as const, step: 8, wet: 2, remaining: 110_000 },
  ];
  const repaired = (classesFirst: boolean) => {
    const w = new RegionalWeather(SEED, 'winter');
    if (classesFirst) w.setClasses(classes, true);
    else w.setClasses(Uint8Array.from([0, 0, 0, 0, 0]), false); // the placeholder window
    w.restore(winterRecord(broken), 'winter');
    if (!classesFirst) {
      // before the real classes arrive nothing is touched (see (d)) ...
      assert.deepEqual(states(w), broken.map((r) => [r.kind, r.step, r.wet, r.remaining]));
      w.setClasses(classes, true); // ... and the first real assignment verifies EVERY slot, slot 0 included (its class did not change)
    }
    return w;
  };
  for (const classesFirst of [true, false]) {
    const w = repaired(classesFirst);
    for (const slot of [0, 3, 4]) {
      const saved = broken[slot];
      const expected = drawWeather(SEED, slot, saved.step, 'winter', slot, saved.wet);
      const now = w.slotState(slot);
      assert.equal(now.kind, expected.kind, `slot ${slot} is re-drawn from its own saved state (classes first: ${classesFirst})`);
      assert.equal(now.step, saved.step + 1);
      assert.equal(now.remainingMs, expected.intervalMs);
      assert.equal(now.wetStreak, expected.kind === 'clear' ? 0 : saved.wet + 1);
      assert.ok(W_ELIGIBLE(slot, now.kind), `slot ${slot} now shows ${now.kind}, which its rules allow`);
    }
    // the valid slots are untouched
    assert.deepEqual(states(w).filter((_, k) => k === 1 || k === 2), [['rain', 5, 1, 140_000], ['light-snow', 6, 1, 130_000]]);
    // the repair is shown at once: nothing is fading
    for (const kind of ['clear', 'rain', 'light-snow', 'heavy-snow'] as const) for (let k = 0; k < 5; k++) assert.ok(w.alpha(kind, k) === 0 || w.alpha(kind, k) === 1);
  }
  // deterministic: the same record repairs to the same state every time, whichever order the classes arrived in
  assert.deepEqual(states(repaired(true)), states(repaired(false)));
  assert.deepEqual(states(repaired(true)), states(repaired(true)));
  // and a repaired state is a valid automatic record
  const w = repaired(false);
  valid({ ...core.defaultCityPresentationV2(), weather: w.persist() });
});

/** Whether a kind is one the rules allow for the slot's class (slot = class in these tests) in winter. */
const W_ELIGIBLE = (slot: number, kind: string): boolean => {
  const table: Record<number, string[]> = { 0: ['clear', 'light-snow'], 1: ['clear', 'light-snow', 'rain'], 2: ['clear', 'light-snow', 'heavy-snow'], 3: ['clear', 'light-snow', 'heavy-snow'], 4: ['clear', 'light-snow'] };
  return table[slot].includes(kind);
};

test('(c) THE RECORD-LEVEL INVARIANTS: ranges, the wet-streak maximum, the clear/wet relationship and the step bounds are refused by the core check, so a repaired or restored state is always inside them', () => {
  const auto = (regions: object[]) => ({ ...core.defaultCityPresentationV2(), weather: { mode: 'auto', seed: 1, regions } });
  const ok = REACHABLE_WINTER.map((r) => ({ ...r }));
  assert.equal(core.checkCityPresentationV2(auto(ok)), null);
  const bad: Array<[string, object[], string]> = [
    ['a time left over the range', ok.map((r, i) => (i === 0 ? { ...r, remaining: 300_001 } : r)), 'region time left out of range'],
    ['a negative time left', ok.map((r, i) => (i === 1 ? { ...r, remaining: -1 } : r)), 'region time left out of range'],
    ['a wet streak over the pinned maximum', ok.map((r, i) => (i === 2 ? { ...r, wet: 3 } : r)), 'region wet streak out of range'],
    ['a clear region with a wet streak', ok.map((r, i) => (i === 0 ? { ...r, kind: 'clear', wet: 2 } : r)), 'region wet streak disagrees with its kind'],
    ['a wet region with no streak', ok.map((r, i) => (i === 3 ? { ...r, wet: 0 } : r)), 'region wet streak disagrees with its kind'],
    ['a draw index at the bound plus one', ok.map((r, i) => (i === 4 ? { ...r, step: 1_000_000 } : r)), 'region draw index out of range'],
  ];
  for (const [what, regions, reason] of bad) assert.equal(core.checkCityPresentationV2(auto(regions)), reason, what);
  // the migration refuses the same records (it uses the same check), so nothing outside the invariants reaches the engine
  assert.equal(core.migrateCityPresentation(auto(bad[3][1])).ok, false);
  // a record inside the invariants that names an ineligible kind passes the core check (eligibility needs the real classes) and is repaired by (b)
  assert.equal(core.checkCityPresentationV2(auto(ok.map((r, i) => (i === 0 ? { ...r, kind: 'heavy-snow', wet: 1 } : r)))), null);
});

test('(d) THE PRE-LAYOUT WINDOW NEVER RE-DRAWS VALID HIGHLANDS OR WOODLAND WEATHER: against the placeholder (every slot City) it acts on nothing, in motion or in a season change', () => {
  const w = new RegionalWeather(SEED, 'winter');
  // the placeholder classes (all City) are declared first (OfficeState's constructor does it), the record is restored, and frames run:
  // Woodland heavy snow would be ineligible as City
  w.setClasses(Uint8Array.from([0, 0, 0, 0, 0]), false);
  w.restore(winterRecord(REACHABLE_WINTER), 'winter');
  for (let k = 0; k < 8; k++) w.update(0.25, true);
  assert.deepEqual(states(w), REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet, r.remaining - 2_000]), 'nothing was re-drawn in the window');
  // the real classes arrive: still nothing to repair
  w.setClasses(classes, true);
  assert.deepEqual(states(w), REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet, r.remaining - 2_000]));
  // a season change inside the window defers its check to the real classes instead of acting on the placeholder
  const x = new RegionalWeather(SEED, 'autumn');
  x.setClasses(Uint8Array.from([0, 0, 0, 0, 0]), false); // the placeholder window comes first
  x.restore(
    winterRecord([
      { kind: 'rain', step: 1, wet: 1, remaining: 100_000 }, // an autumn City may rain; a winter City may not
      { kind: 'clear', step: 2, wet: 0, remaining: 100_000 },
      { kind: 'clear', step: 3, wet: 0, remaining: 100_000 },
      { kind: 'light-snow', step: 4, wet: 1, remaining: 100_000 }, // an autumn AND a winter Highlands may have light snow
      { kind: 'clear', step: 5, wet: 0, remaining: 100_000 },
    ]),
    'autumn',
  );
  x.setSeason('winter', true); // inside the window
  assert.equal(x.slotState(3).step, 4, 'the Highlands light snow was not re-drawn against the placeholder');
  assert.equal(x.slotState(0).step, 1, 'and nothing else was acted on yet');
  x.setClasses(classes, true); // the real classes: now every region is checked for WINTER
  assert.equal(x.slotState(3).step, 4, 'a valid winter Highlands keeps its light snow');
  assert.equal(x.slotState(3).kind, 'light-snow');
  assert.equal(x.slotState(0).step, 2, 'the City rain is not eligible in winter: re-drawn once');
  assert.ok(W_ELIGIBLE(0, x.slotState(0).kind));
  // later layout changes keep the earlier rule: a slot whose class changed keeps a still-valid kind, and re-draws only an invalid one
  const y = new RegionalWeather(SEED, 'winter', Uint8Array.from([0, 0, 0, 0, 0]));
  y.setClasses(Uint8Array.from([0, 0, 0, 0, 0]), true);
  y.restore(winterRecord([{ ...REACHABLE_WINTER[0] }, { ...REACHABLE_WINTER[1], kind: 'light-snow', wet: 1 }, { ...REACHABLE_WINTER[3] }, { ...REACHABLE_WINTER[3] }, { ...REACHABLE_WINTER[4] }]), 'winter');
  y.setClasses(classes, true);
  assert.equal(y.slotState(3).step, REACHABLE_WINTER[3].step, 'class City to Highlands: light snow is still valid, kept');
  assert.equal(y.slotState(2).step, REACHABLE_WINTER[3].step, 'class City to Woodland: light snow is still valid, kept');
});

/** The draw a slot makes at `step` with `wet` for class `cls` in winter (the pure schedule), to compare a product draw with. */
const winterDraw = (slot: number, step: number, cls: number, wet = 1) => drawWeather(SEED, slot, step, 'winter', cls, wet);
/** A draw index at which the REAL class of `slot` (Woodland 2, Highlands 3) draws a different kind than the placeholder class City would: so a draw made against the placeholder is visible. */
const stepWhereClassMatters = (slot: number): number => {
  for (let step = 0; step < 500; step++) if (winterDraw(slot, step, classes[slot]).kind !== winterDraw(slot, step, 0).kind) return step;
  throw new Error(`no draw index where class ${classes[slot]} differs from City for slot ${slot}`);
};

test('(e) EXPIRED RESTORES WAIT FOR THE REAL CLASSES: a Woodland or Highlands slot restored with no time left, or less than one step, draws only once the real classes are known, and then with them', () => {
  const placeholder = Uint8Array.from([0, 0, 0, 0, 0]);
  const slots = [2, 3];
  const at = Object.fromEntries(slots.map((k) => [k, stepWhereClassMatters(k)]));
  // the premise is real: against the placeholder City class these slots would draw something else
  for (const k of slots) assert.notEqual(winterDraw(k, at[k], classes[k]).kind, winterDraw(k, at[k], 0).kind);
  for (const remaining of [0, 1, 50, 99, 100]) {
    const w = new RegionalWeather(SEED, 'winter');
    w.setClasses(placeholder, false); // the default layout's placeholder, before the real layout arrives
    const regions = REACHABLE_WINTER.map((r) => ({ ...r }));
    for (const k of slots) regions[k] = { kind: 'light-snow', step: at[k], wet: 1, remaining };
    w.restore(winterRecord(regions), 'winter');
    for (let f = 0; f < 5; f++) w.update(0.1, true); // half a second of frames in the placeholder window
    for (const k of slots) {
      const x = w.slotState(k);
      assert.deepEqual([x.kind, x.step, x.wetStreak], ['light-snow', at[k], 1], `remaining ${remaining}: slot ${k} drew against the placeholder`);
      assert.equal(x.remainingMs, 0, `remaining ${remaining}: slot ${k} waits at zero, neither negative nor drawn`);
    }
    w.setClasses(classes, true); // the real classes are known
    for (const k of slots) assert.equal(w.slotState(k).step, at[k], 'a valid kind is not touched when the classes arrive');
    w.update(0.1, true); // the first step with the real classes
    for (const k of slots) {
      const drawn = winterDraw(k, at[k], classes[k]);
      const x = w.slotState(k);
      assert.deepEqual([x.kind, x.step, x.remainingMs], [drawn.kind, at[k] + 1, drawn.intervalMs], `remaining ${remaining}: slot ${k} drew once, with its real class`);
    }
  }
});

test('(e) THROUGH THE REAL OFFICESTATE: expired Woodland and Highlands slots restored before the layout arrives draw, after it, with the real classes', () => {
  const slots = [2, 3];
  const at = Object.fromEntries(slots.map((k) => [k, stepWhereClassMatters(k)]));
  for (const remaining of [0, 1, 99]) {
    const regions = REACHABLE_WINTER.map((r) => ({ ...r }));
    for (const k of slots) regions[k] = { kind: 'light-snow', step: at[k], wet: 1, remaining };
    const saved = record({ season: { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 }, weather: winterRecord(regions) });
    valid(saved);
    const os = new OfficeState(); // the placeholder layout, as in the real App
    os.loadCityRecord(structuredClone(saved));
    for (let f = 0; f < 4; f++) os.update(0.1);
    const waiting = os.cityRecord().weather.regions;
    for (const k of slots) assert.deepEqual([waiting[k].kind, waiting[k].step], ['light-snow', at[k]], `remaining ${remaining}: slot ${k} waited for the layout`);
    os.rebuildFromLayout(structuredClone(layout)); // the shipped metropolis: its region classes are the five districts
    os.update(0.1);
    const now = os.cityRecord().weather.regions;
    for (const k of slots) {
      const drawn = winterDraw(k, at[k], classes[k]);
      assert.deepEqual([now[k].kind, now[k].step], [drawn.kind, at[k] + 1], `remaining ${remaining}: slot ${k} drew with its real class`);
    }
  }
}, 120_000);

test('(f) AN ADOPTED DEFAULT LAYOUT IS AUTHORITATIVE: once the office is told no other layout is coming, its region classes are the real ones, the deferred eligibility repair runs, and choosing Winter leaves no Rain or Heavy snow over a City', () => {
  // every region of the default layout is the City class; Winter allows only clear and light snow there (design D11)
  const wet = (os: OfficeState) => os.cityRecord().weather.regions.map((r) => r.kind).filter((kind) => kind === 'rain' || kind === 'heavy-snow');
  const reload = (): OfficeState => new OfficeState(); // the default layout, built as a placeholder (the real App's first state)
  const probe = reload();
  assert.ok(probe.cityRecord().weather.regions.some((r) => r.kind === 'rain'), 'the seeded Automatic start has Rain over a City, so Winter has something to repair');

  // THE PLACEHOLDER WINDOW (the office has not been told the layout is final): the repair is deferred, by design
  const waiting = reload();
  waiting.chooseCitySeason('winter');
  waiting.update(0.1);
  assert.ok(wet(waiting).length > 0, 'before the layout is final the Winter repair waits for the real classes');

  // ... ADOPTING the default layout (the App's branch for "no saved layout") makes its classes the real ones, and the repair runs at once
  waiting.adoptLayout();
  waiting.update(0.1);
  assert.deepEqual(wet(waiting), [], 'adopted: no Rain or Heavy snow over a City in Winter');

  // chosen AFTER the adoption: repaired immediately, with no frame in between
  const adopted = reload();
  adopted.adoptLayout();
  adopted.chooseCitySeason('winter');
  assert.deepEqual(wet(adopted), [], 'chosen after the adoption: repaired at once');

  // adopting changes nothing else: a valid state is untouched, and adopting twice is the same as once
  const valid2 = reload();
  valid2.adoptLayout();
  const before = JSON.stringify(valid2.cityRecord());
  valid2.adoptLayout();
  assert.equal(JSON.stringify(valid2.cityRecord()), before, 'adopting again changes nothing');
});

test('(f) THE APP ADOPTS THE DEFAULT LAYOUT: the layoutLoaded handler\'s default branch calls adoptLayout (a source pin for the one line the unit test cannot reach)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src/hooks/useExtensionMessages.ts'), 'utf-8');
  const branch = src.slice(src.indexOf('// Default layout'), src.indexOf('// Add buffered agents now that layout'));
  assert.ok(/os\.adoptLayout\(\)/.test(branch), 'the default branch adopts the layout');
  // and the other branch builds the loaded one, which also makes it real
  assert.ok(/os\.rebuildFromLayout\(layout\)/.test(src));
});

test('NOTHING RUNS WHILE THE VIEW IS CLOSED OR HIDDEN: with no game-loop step the record does not move, whatever the wall clock does', () => {
  const e = new CityEffects();
  run(e, 50_000);
  const before = JSON.stringify(e.record());
  // hours of wall clock with no step (a hidden tab, a closed page, a sleeping computer): the engines read no clock of their own
  const realNow = Date.now;
  try {
    Date.now = () => realNow() + 6 * 3_600_000;
    assert.equal(JSON.stringify(e.record()), before);
    assert.equal(JSON.stringify(e.record()), before);
  } finally {
    Date.now = realNow;
  }
});

test('PERSIST REVISION moves on choices, season changes and automatic draws, and NEVER per frame', () => {
  const e = new CityEffects();
  const r0 = e.persistRevision;
  // a held season and a held weather: a thousand frames change nothing worth saving
  e.chooseSeason('autumn', true);
  e.chooseWeather('rain', true);
  const held = e.persistRevision;
  assert.ok(held > r0);
  run(e, 250_000);
  assert.equal(e.persistRevision, held, '1,000 frames of held state: no revision');
  // Automatic: the revision moves exactly once per season change or draw, not per frame
  const a = new CityEffects();
  const start = a.persistRevision;
  let draws = 0;
  const steps = (): number => [0, 1, 2, 3, 4].reduce((n, k) => n + a.regional.slotState(k).step, 0);
  let lastSteps = steps();
  let lastSeason = a.seasons.season;
  let changes = 0;
  for (let t = 0; t < 3_600_000; t += 250) {
    a.update(0.25, 0, true);
    const s = steps();
    if (s !== lastSteps) {
      draws += s - lastSteps;
      lastSteps = s;
    }
    if (a.seasons.season !== lastSeason) {
      changes++;
      lastSeason = a.seasons.season;
    }
  }
  assert.ok(draws > 25 && changes === 2, `${draws} draws and ${changes} season changes in an hour`);
  // EXACTLY: each draw bumps the revision once, and each season change bumps it twice (the season engine and the weather's re-check of its
  // kinds for the new season); the re-draws that re-check causes are draws and are counted above. Nothing moves per frame.
  assert.equal(a.persistRevision - start, draws + 2 * changes, `${a.persistRevision - start} revisions for ${draws} draws and ${changes} season changes`);
  assert.ok(a.persistRevision - start < 3_600_000 / 250 / 10, 'far fewer than one per frame');
});

test('persistsRunning says whether a saved clock is SET to run, and runningAdvanceMs moves only when a step ACTUALLY ran one', () => {
  const e = new CityEffects();
  assert.equal(e.persistsRunning(), true); // Automatic seasons and weather
  e.chooseWeather('clear', true);
  assert.equal(e.persistsRunning(), true); // seasons still run
  e.pauseSeasons();
  assert.equal(e.persistsRunning(), false); // paused seasons, held weather
  e.chooseWeather('auto', true);
  assert.equal(e.persistsRunning(), true); // Automatic weather runs
  e.chooseWeather('rain', true);
  e.resumeSeasons();
  e.chooseSeason('winter', true);
  assert.equal(e.persistsRunning(), false); // everything held

  // THE ADVANCE: it is the running time the loop's steps gave a clock that is set to run, and nothing else
  const f = new CityEffects();
  assert.equal(f.runningAdvanceMs, 0);
  // no step, however long the wall clock runs (a hidden view): nothing advanced
  assert.equal(f.runningAdvanceMs, 0, 'no step, no advance');
  f.update(0.25, 0, true);
  assert.equal(f.runningAdvanceMs, 250);
  f.update(0, 0, true);
  assert.equal(f.runningAdvanceMs, 250, 'a zero-length step advances nothing');
  for (let k = 0; k < 3; k++) f.update(0.1, 0, true);
  assert.ok(Math.abs(f.runningAdvanceMs - 550) < 1e-6, `${f.runningAdvanceMs}`);
  // a clock set not to run gives no advance, step after step
  f.chooseWeather('rain', true);
  f.chooseSeason('winter', true);
  const held = f.runningAdvanceMs;
  for (let k = 0; k < 40; k++) f.update(0.25, 0, true);
  assert.equal(f.runningAdvanceMs, held, 'held season and held weather: steps advance no saved clock');
  // set to run again: it moves again
  f.chooseAutomaticSeasons();
  f.update(0.25, 0, true);
  assert.equal(f.runningAdvanceMs, held + 250);
});

// ── when a save is due ────────────────────────────────────────────────────────────────────────────────────

test('THE SAVE GATE: a transition is due after the minimum gap, an ADVANCED running clock at the checkpoint rate, and an idle, held or merely "running" city never', () => {
  const g = new CitySaveGate(1_000_000, 7, 500);
  // nothing changed and no clock advanced: never due, however long (the advance argument is the counter's value at the last save)
  for (const dt of [0, 1_999, 2_000, 60_000, 3_600_000, 86_400_000]) assert.equal(g.due(1_000_000 + dt, 7, 500), false, `idle ${dt}`);
  // a transition (the revision moved): not before the minimum gap, then due
  assert.equal(g.due(1_000_000 + 1_999, 8, 500), false);
  assert.equal(g.due(1_000_000 + 2_000, 8, 500), true);
  // a clock that advanced with no transition: due at exactly the checkpoint, not a ms sooner
  assert.equal(g.due(1_000_000 + 59_999, 7, 750), false);
  assert.equal(g.due(1_000_000 + 60_000, 7, 750), true);
  // RUNNING BUT NOT ADVANCED is not due (a hidden view: the clock is set to run, the loop does not step): hours of wall clock, nothing new
  assert.equal(g.due(1_000_000 + 60_000, 7, 500), false);
  assert.equal(g.due(1_000_000 + 3 * 60_000, 7, 500), false);
  assert.equal(g.due(1_000_000 + 86_400_000, 7, 500), false);
  // after a save the clocks restart, from the advance it saw
  g.sent(2_000_000, 8, 900);
  assert.equal(g.due(2_000_000, 8, 900), false);
  assert.equal(g.due(2_000_000 + 59_999, 8, 901), false);
  assert.equal(g.due(2_000_000 + 60_000, 8, 901), true);
  assert.equal(g.due(2_000_000 + 60_000, 8, 900), false, 'at the checkpoint, but nothing advanced since the save');
});

test('A BURST of transitions is ONE save: revisions inside the minimum gap wait for it', () => {
  const g = new CitySaveGate(0, 0);
  g.sent(10_000, 1);
  let saves = 0;
  let revision = 1;
  for (let t = 10_000; t <= 14_000; t += 250) {
    revision++; // five regions and the season all change within four seconds
    if (g.due(t, revision, 0)) {
      saves++;
      g.sent(t, revision);
    }
  }
  // the gap is 2,000 ms, so within 4,000 ms there can be at most two saves, never one per change
  assert.ok(saves <= 3, `${saves} saves for ${(14_000 - 10_000) / 250 + 1} changes`);
  assert.ok(saves >= 1);
});

test('HIDING the view saves once more when anything changed or a clock ADVANCED since the last save, under the same minimum gap, and never an idle city', () => {
  const g = new CitySaveGate(0, 3, 100);
  assert.equal(g.dueOnHide(1_999, 4, 200), false, 'inside the minimum gap');
  assert.equal(g.dueOnHide(2_000, 3, 100), false, 'nothing changed and no clock advanced');
  assert.equal(g.dueOnHide(2_000, 4, 100), true, 'something changed');
  assert.equal(g.dueOnHide(2_000, 3, 101), true, 'a clock advanced');
  g.sent(5_000, 4, 300);
  assert.equal(g.dueOnHide(5_500, 5, 400), false);
  // a second leave event (pagehide after the view went hidden) with nothing new since the first save writes nothing
  assert.equal(g.dueOnHide(60_000, 4, 300), false);
});

test('SAVE RATE over two hours of real engine time with a poll every second: never closer than the minimum gap, bounded by transitions and checkpoints, zero when idle', () => {
  const polls = (e: CityEffects, hours: number) => {
    const g = new CitySaveGate(0, e.persistRevision, e.runningAdvanceMs);
    const saveTimes: number[] = [];
    let changedPolls = 0;
    let lastPolled = e.persistRevision;
    let now = 0;
    for (let sec = 1; sec <= hours * 3600; sec++) {
      for (let k = 0; k < 4; k++) {
        e.update(0.25, 0, true);
        now += 250;
      }
      const rev = e.persistRevision;
      if (rev !== lastPolled) {
        changedPolls++;
        lastPolled = rev;
      }
      if (g.due(now, rev, e.runningAdvanceMs)) {
        saveTimes.push(now);
        g.sent(now, rev, e.runningAdvanceMs);
      }
    }
    return { saveTimes, changedPolls };
  };
  const running = polls(new CityEffects(), 2);
  for (let i = 1; i < running.saveTimes.length; i++)
    assert.ok(running.saveTimes[i] - running.saveTimes[i - 1] >= C.CITY_SAVE_MIN_GAP_MS, `saves ${i - 1} and ${i} are ${running.saveTimes[i] - running.saveTimes[i - 1]} ms apart`);
  const checkpoints = Math.floor((2 * 3_600_000) / C.CITY_SAVE_CHECKPOINT_MS);
  assert.ok(running.saveTimes.length <= running.changedPolls + checkpoints, `${running.saveTimes.length} saves > ${running.changedPolls} transition polls + ${checkpoints} checkpoints`);
  assert.ok(running.saveTimes.length >= checkpoints - 1, 'checkpoints do happen while a clock runs');
  assert.ok(running.saveTimes.length < (2 * 3600) / 4, 'orders of magnitude below one save per poll');
  // an idle city (held season, held weather) saves nothing in two hours
  const idle = new CityEffects();
  idle.chooseSeason('winter', true);
  idle.chooseWeather('light-snow', true);
  const g2 = polls(idle, 2);
  // the choices above moved the revision once, before the gate started: nothing is due afterwards
  assert.deepEqual(g2.saveTimes, []);
});

/** An event target with the one property the saver reads, and a count of its live listeners (the stop function must remove every one). */
class FakeTarget extends EventTarget {
  visibilityState = 'visible';
  listeners = 0;
  override addEventListener(type: string, listener: EventListenerOrEventListenerObject | null): void {
    this.listeners++;
    super.addEventListener(type, listener);
  }
  override removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null): void {
    this.listeners--;
    super.removeEventListener(type, listener);
  }
}

test('A HIDDEN VIEW REWRITES NOTHING [DETERMINISTIC: vitest FAKE TIMERS and injected event targets, no browser and no real time; the real saver wiring (startCitySaving), the real gate and a real CityEffects stepped by hand]: hidden for 3.5 checkpoints is one on-hide save and no more; visible and stepping, checkpoints resume at the bounded rate; a choice is saved at once', () => {
  vi.useFakeTimers();
  vi.setSystemTime(5_000_000);
  try {
    const e = new CityEffects();
    // the premise: ONE running clock (Automatic seasons, a 30-minute interval) and no automatic draw that could be a transition, so the
    // counts below are the checkpoint rule alone
    e.chooseWeather('clear', true);
    e.update(0.25, 0, true);
    const doc = new FakeTarget();
    const win = new FakeTarget();
    const sends: number[] = [];
    const saver = new CitySaver(() => sends.push(Date.now()));
    let ready = false;
    const stop = startCitySaving(saver, () => e, () => ready, { doc, win });
    const hide = () => {
      doc.visibilityState = 'hidden';
      doc.dispatchEvent(new Event('visibilitychange'));
    };
    const show = () => {
      doc.visibilityState = 'visible';
      doc.dispatchEvent(new Event('visibilitychange'));
    };
    /** One second of a VISIBLE view: the loop steps four times, then the polling interval fires. */
    const visibleSecond = () => {
      for (let k = 0; k < 4; k++) e.update(0.25, Date.now(), true);
      vi.advanceTimersByTime(1000);
    };
    /** One second of a HIDDEN view: the loop does not step (frameStep's hidden guard); the polling interval still fires. */
    const hiddenSecond = () => vi.advanceTimersByTime(1000);
    const t0 = Date.now();

    // 0. nothing is saved before the saved record has been loaded, however long the view runs, hides or leaves
    for (let sec = 0; sec < 70; sec++) visibleSecond();
    hide();
    win.dispatchEvent(new Event('pagehide'));
    show();
    assert.deepEqual(sends, [], 'nothing before the load');
    ready = true;
    saver.loaded(Date.now(), e); // the city as loaded: nothing is due

    // 1. visible and stepping for 90 s: a clock advanced, so ONE checkpoint, 60 s after the load
    const loadedAt = Date.now();
    for (let sec = 0; sec < 90; sec++) visibleSecond();
    assert.deepEqual(sends, [loadedAt + 60_000], 'one checkpoint at the bounded rate');

    // 2. a viewer's CHOICE is saved at once (the hook's choose calls save) and re-marks the gate: the next poll finds nothing new
    e.chooseSeason('winter', true);
    saver.save(Date.now(), e);
    assert.equal(sends.length, 2, 'the choice was saved immediately');
    vi.advanceTimersByTime(1000);
    assert.equal(sends.length, 2, 'and it is not saved a second time by the poll');
    e.chooseAutomaticSeasons(); // back to a running clock
    saver.save(Date.now(), e);
    for (let sec = 0; sec < 30; sec++) visibleSecond();
    assert.equal(sends.length, 3, 'the second choice was saved immediately, and 30 s of stepping is not yet a checkpoint');

    // 3. the view is hidden: ONE save as the city stood (a clock advanced for 30 s since the last save)
    const beforeHide = sends.length;
    hide();
    assert.equal(sends.length, beforeHide + 1, 'one on-hide save');
    const atHide = JSON.stringify(e.record());
    const advanceAtHide = e.runningAdvanceMs;

    // 4. hidden for 210 s (3.5 checkpoints): the interval keeps firing, the loop never steps. NOTHING more is saved, and nothing moved
    const hiddenFor = 3.5 * C.CITY_SAVE_CHECKPOINT_MS;
    for (let sec = 0; sec < hiddenFor / 1000; sec++) hiddenSecond();
    assert.equal(sends.length, beforeHide + 1, 'exactly the one on-hide save, and zero more');
    assert.equal(e.runningAdvanceMs, advanceAtHide, 'no clock advanced while hidden');
    assert.equal(JSON.stringify(e.record()), atHide, 'the record is exactly what the on-hide save wrote');
    // a second leave event (pagehide following the hide, or a late one) writes nothing either
    win.dispatchEvent(new Event('pagehide'));
    hide();
    assert.equal(sends.length, beforeHide + 1);

    // NEGATIVE CONTROL: the PREVIOUS rule (a clock "running" by its mode flags) over this very hidden period would have rewritten the
    // unchanged record at every checkpoint, which is what this test exists to prevent
    let lastAt = Date.now() - hiddenFor;
    let oldRuleSaves = 0;
    for (let t = lastAt + 1000; t <= Date.now(); t += 1000)
      if (t - lastAt >= C.CITY_SAVE_CHECKPOINT_MS && e.persistsRunning()) {
        oldRuleSaves++;
        lastAt = t;
      }
    assert.equal(oldRuleSaves, 3, 'the old mode-flag rule rewrote an unchanged record three times in 210 s');

    // 5. visible again and stepping for 180 s: checkpoints resume, never closer than the bounded rate
    show();
    const before = sends.length;
    for (let sec = 0; sec < 180; sec++) visibleSecond();
    const after = sends.slice(before);
    assert.equal(after.length, 3, 'one at the first poll after a clock advanced, then one every checkpoint');
    for (let i = 1; i < after.length; i++) assert.equal(after[i] - after[i - 1], C.CITY_SAVE_CHECKPOINT_MS, 'exactly the bounded rate');
    assert.ok(Date.now() - t0 > 500_000, 'about ten minutes of fake time, in milliseconds of real time');

    // 6. stopped: every listener is removed and the interval is cleared
    stop();
    assert.equal(doc.listeners, 0);
    assert.equal(win.listeners, 0);
    const stopped = sends.length;
    for (let sec = 0; sec < 130; sec++) visibleSecond();
    assert.equal(sends.length, stopped, 'a stopped saver sends nothing');
  } finally {
    vi.useRealTimers();
  }
});

// ── through the real OfficeState on the shipped world ───────────────────────────────────────────────────

let layout: OfficeLayout;
beforeAll(() => {
  loadBundledCatalog();
  layout = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));
}, 120_000);

test('THROUGH THE REAL OFFICESTATE: a saved winter city loads before the layout, SURVIVES the pre-layout frames and the layout arriving, and draws as saved', () => {
  const saved = record({
    lighting: 'dusk',
    traffic: 'low',
    season: { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 },
    weather: winterRecord(REACHABLE_WINTER),
  });
  valid(saved);
  const os = new OfficeState(); // the default layout, as in the real App: the saved record arrives before the real layout
  os.loadCityRecord(structuredClone(saved));
  // three frames on the placeholder layout (every region reads City): nothing is re-drawn
  for (let k = 0; k < 3; k++) os.update(0.1);
  assert.deepEqual(os.cityRecord().weather.regions.map((r) => [r.kind, r.step, r.wet]), REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet]));
  os.rebuildFromLayout(structuredClone(layout)); // the shipped metropolis arrives: its region classes are the five districts
  os.update(0.1); // syncs the region classes
  const back = os.cityRecord();
  assert.equal(back.lighting, 'dusk');
  assert.equal(back.traffic, 'low');
  assert.deepEqual(back.season, { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 });
  // every saved kind is valid for its real class in winter: each region keeps its kind, draw index, wet streak and (to the 0.4 s that ran) time left
  assert.deepEqual(
    back.weather.regions.map((r) => [r.kind, r.step, r.wet, r.remaining]),
    REACHABLE_WINTER.map((r) => [r.kind, r.step, r.wet, r.remaining - 400]),
  );
  const input = os.cityRenderInput();
  assert.ok(input !== null && input.season?.season === 'winter' && input.weather !== null);
  assert.equal(os.cityAtmosphere().season.mode, 'manual');
  // an inconsistent record (a City with automatic heavy snow in winter) is repaired when the real layout's classes arrive
  const bad = record({
    season: { mode: 'manual', season: 'winter', paused: false, remaining: 1_800_000 },
    weather: winterRecord([{ kind: 'heavy-snow', step: 4, wet: 1, remaining: 150_000 }, ...REACHABLE_WINTER.slice(1)]),
  });
  valid(bad);
  const os2 = new OfficeState();
  os2.loadCityRecord(structuredClone(bad));
  os2.update(0.1);
  assert.equal(os2.cityRecord().weather.regions[0].kind, 'heavy-snow', 'untouched before the layout');
  os2.rebuildFromLayout(structuredClone(layout));
  os2.update(0.1);
  const fixed = os2.cityRecord().weather.regions[0];
  assert.equal(fixed.step, 5);
  assert.ok(W_ELIGIBLE(0, fixed.kind), `the City now shows ${fixed.kind}`);
  assert.equal(fixed.kind, drawWeather(SEED, 0, 4, 'winter', 0, 1).kind);
}, 120_000);

test('a SEASON RECORD that is the default loads as the default (a new city loads as itself)', () => {
  const e = new CityEffects();
  e.loadRecord(core.defaultCityPresentationV2(), 0, true);
  const s = new SeasonEngine();
  assert.deepEqual(e.seasons.persist(), s.persist());
});
