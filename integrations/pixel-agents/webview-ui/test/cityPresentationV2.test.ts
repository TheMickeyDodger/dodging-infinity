/**
 * ITEM 18 (W1): THE v2 PRESENTATION CONTRACT AND THE ONE MIGRATION, asserted exactly. The contract is strict at every level (exact keys, enums,
 * integer ranges, an exact region count); its STORED form (what the server writes) is compact and has the pinned 256-byte ceiling of the saved city
 * choices; the migration is ONE pure function used by the server (when it reads a
 * file) and by the webview (when it applies a record). A v1 record keeps its lighting and traffic, its weather becomes the same MANUAL choice, and
 * the seasons start Automatic from summer with a full interval; anything else is refused whole with a reason and nothing is partly kept.
 *
 * Every numeric bound is also pinned in livingCityLocalBounds.test.ts; the equalities between the core's bounds and the webview's constants
 * (the same numbers in two layers that must never drift) are asserted here.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import * as core from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import { WEATHER_KINDS, WEATHER_MODES } from '../src/office/city/cityRegionWeather.ts';
import { SEASONS } from '../src/office/city/citySeason.ts';

const record = (): core.CityPresentationV2 => structuredClone(core.defaultCityPresentationV2());
const refused = (value: unknown): string | null => core.checkCityPresentationV2(value);
type Key = string | number;
const parent = (r: unknown, path: Key[]): Record<Key, unknown> =>
  path.slice(0, -1).reduce<unknown>((o, k) => (o as Record<Key, unknown>)[k], r) as Record<Key, unknown>;
/** Set (or delete) the value at a path of a plain JSON object: how these tests make each malformed shape from a valid record. */
const setAt = (r: unknown, path: Key[], value: unknown): void => {
  parent(r, path)[path[path.length - 1]] = value;
};
const delAt = (r: unknown, path: Key[]): void => {
  delete parent(r, path)[path[path.length - 1]];
};
const json = (): unknown => JSON.parse(JSON.stringify(record()));
/** The compact text the server would write for a record (it must be admissible), and the record read back from such a text. */
const encoded = (r: unknown): string => {
  const e = core.encodeStoredCityPresentationV2(r);
  assert.ok(e.ok, e.ok ? '' : e.reason);
  return e.text;
};
const unpacked = (text: string): core.CityPresentationV2 => {
  const m = core.unpackCityPresentationV2(JSON.parse(text));
  assert.ok(m.ok, m.ok ? '' : m.reason);
  return m.state;
};

test('the v2 contract bounds and lists are exactly these values', () => {
  assert.equal(core.CITY_PRESENTATION_V2_VERSION, 2);
  assert.equal(core.CITY_PRESENTATION_V2_MAX_BYTES, 256);
  assert.equal(core.CITY_PRESENTATION_V2_MAX_BYTES, core.CITY_PRESENTATION_MAX_BYTES, 'the saved city choices keep their documented 256-byte budget');
  assert.equal(core.CITY_PRESENTATION_REGION_SLOTS, 5);
  assert.equal(core.CITY_PRESENTATION_SEASON_REMAINING_MAX_MS, 1_800_000);
  assert.equal(core.CITY_PRESENTATION_REGION_REMAINING_MAX_MS, 300_000);
  assert.equal(core.CITY_PRESENTATION_WET_STREAK_MAX, 2);
  assert.equal(core.CITY_PRESENTATION_STEP_MAX, 999_999);
  assert.equal(core.CITY_PRESENTATION_SEED_MAX, 4_294_967_295);
  assert.equal(core.CITY_PRESENTATION_DEFAULT_SEED, 0x51a7);
  assert.deepEqual([...core.CITY_SEASON_NAMES], ['spring', 'summer', 'autumn', 'winter']);
  assert.deepEqual([...core.CITY_SEASON_MODES], ['auto', 'manual']);
  assert.deepEqual([...core.CITY_WEATHER_KINDS], ['clear', 'rain', 'light-snow', 'heavy-snow']);
  assert.deepEqual([...core.CITY_WEATHER_V2_MODES], ['auto', 'clear', 'rain', 'light-snow', 'heavy-snow']);
  // v1 is untouched: it is the contract a migrated file was written under
  assert.equal(core.CITY_PRESENTATION_VERSION, 1);
  assert.equal(core.CITY_PRESENTATION_MAX_BYTES, 256);
});

test('the core and the webview agree on every number and name they share (they are two layers that must never drift)', () => {
  assert.deepEqual([...core.CITY_SEASON_NAMES], [...SEASONS]);
  assert.deepEqual([...core.CITY_WEATHER_KINDS], [...WEATHER_KINDS]);
  assert.deepEqual([...core.CITY_WEATHER_V2_MODES], [...WEATHER_MODES]);
  assert.equal(core.CITY_PRESENTATION_SEASON_REMAINING_MAX_MS, C.CITY_SEASON_INTERVAL_MS);
  assert.equal(core.CITY_PRESENTATION_REGION_REMAINING_MAX_MS, C.CITY_WEATHER_INTERVAL_MAX_MS);
  assert.equal(core.CITY_PRESENTATION_WET_STREAK_MAX, C.CITY_WEATHER_MAX_WET_DRAWS);
  assert.equal(core.CITY_PRESENTATION_STEP_MAX + 1, C.CITY_WEATHER_STEP_LIMIT);
  assert.equal(core.CITY_PRESENTATION_REGION_SLOTS, C.CITY_REGION_SLOTS);
  assert.equal(core.CITY_PRESENTATION_DEFAULT_SEED, C.CITY_WEATHER_SCHEDULE_SEED);
});

test('a new city is exactly this: Automatic seasons from summer with a full interval, Automatic weather, day, normal traffic, five quiet regions', () => {
  const d = core.defaultCityPresentationV2();
  assert.deepEqual(d, {
    v: 2,
    lighting: 'day',
    traffic: 'normal',
    season: { mode: 'auto', season: 'summer', paused: false, remaining: 1_800_000 },
    weather: {
      mode: 'auto',
      seed: 0x51a7,
      regions: [0, 1, 2, 3, 4].map(() => ({ kind: 'clear', step: 0, wet: 0, remaining: 0 })),
    },
  });
  assert.equal(core.checkCityPresentationV2(d), null);
  // each call is its own object: a caller may change it
  assert.ok(core.defaultCityPresentationV2() !== d && core.defaultCityPresentationV2().weather.regions !== d.weather.regions);
});

test('EVERY admissible enumeration is accepted, at the minimum and at the maximum of every number, and its STORED form round-trips and fits the ceiling', () => {
  let n = 0;
  let smallest = Infinity;
  let largest = 0;
  for (const lighting of core.CITY_LIGHTING_MODES)
    for (const traffic of core.CITY_TRAFFIC_MODES)
      for (const mode of core.CITY_SEASON_MODES)
        for (const season of core.CITY_SEASON_NAMES)
          for (const paused of [true, false])
            for (const wm of core.CITY_WEATHER_V2_MODES)
              for (const kind of core.CITY_WEATHER_KINDS)
                for (const big of [false, true]) {
                  const r = record();
                  r.lighting = lighting;
                  r.traffic = traffic;
                  r.season = { mode, season, paused, remaining: big ? 1_800_000 : 0 };
                  // in Automatic weather the streak agrees with the kind (clear 0, wet 1 to the maximum); a manual mode may carry any streak
                  const wet = wm === 'auto' ? (kind === 'clear' ? 0 : big ? 2 : 1) : big ? 2 : 0;
                  r.weather = {
                    mode: wm,
                    seed: big ? 4_294_967_295 : 0,
                    regions: [0, 1, 2, 3, 4].map(() => ({ kind, step: big ? 999_999 : 0, wet, remaining: big ? 300_000 : 0 })),
                  };
                  assert.equal(core.checkCityPresentationV2(r), null, JSON.stringify(r).slice(0, 120));
                  const text = encoded(r);
                  assert.ok(text.length <= core.CITY_PRESENTATION_V2_MAX_BYTES, `${text.length} bytes`);
                  assert.deepEqual(unpacked(text), r, 'the stored form loses nothing');
                  smallest = Math.min(smallest, text.length);
                  largest = Math.max(largest, text.length);
                  n++;
                }
  assert.equal(n, 4 * 3 * 2 * 4 * 2 * 5 * 4 * 2);
  // MEASURED over the whole enumeration: the stored record is between these sizes, far inside the 256-byte ceiling
  assert.deepEqual([smallest, largest], [SMALLEST_STORED, WORST_STORED]);
});

test('EVERY malformed shape is refused with a reason, at every level; nothing is half-accepted', () => {
  const mutate = (f: (r: unknown) => void): unknown => {
    const r = json();
    f(r);
    return r;
  };
  const cases: Array<[string, unknown, string]> = [
    ['null', null, 'preferences are not an object'],
    ['an array', [record()], 'preferences are not an object'],
    ['a string', JSON.stringify(record()), 'preferences are not an object'],
    ['a number', 7, 'preferences are not an object'],
    ['no keys', {}, 'preference keys differ from the contract'],
    ['a missing top key', mutate((r) => delAt(r, ['traffic'])), 'preference keys differ from the contract'],
    ['an extra top key', mutate((r) => setAt(r, ['savedAt'], 1)), 'preference keys differ from the contract'],
    ['an extra key carrying a position', mutate((r) => setAt(r, ['x'], 3)), 'preference keys differ from the contract'],
    ['a v1 record', { v: 1, lighting: 'night', weather: 'rain', traffic: 'low' }, 'preference keys differ from the contract'],
    ['the wrong version', mutate((r) => setAt(r, ['v'], 3)), 'preference version unsupported'],
    ['a string version', mutate((r) => setAt(r, ['v'], '2')), 'preference version unsupported'],
    ['an unknown lighting mode', mutate((r) => setAt(r, ['lighting'], 'noon')), 'lighting mode unknown'],
    ['an unknown traffic mode', mutate((r) => setAt(r, ['traffic'], 'jam')), 'traffic mode unknown'],
    ['a non-string mode', mutate((r) => setAt(r, ['traffic'], 3)), 'traffic mode unknown'],
    ['season: not an object', mutate((r) => setAt(r, ['season'], 'winter')), 'season keys differ from the contract'],
    ['season: missing key', mutate((r) => delAt(r, ['season', 'paused'])), 'season keys differ from the contract'],
    ['season: extra key', mutate((r) => setAt(r, ['season', 'extra'], 1)), 'season keys differ from the contract'],
    ['season: unknown mode', mutate((r) => setAt(r, ['season', 'mode'], 'held')), 'season mode unknown'],
    ['season: unknown season', mutate((r) => setAt(r, ['season', 'season'], 'monsoon')), 'season unknown'],
    ['season: pause is not a boolean', mutate((r) => setAt(r, ['season', 'paused'], 1)), 'season pause flag is not a boolean'],
    ['season: time left negative', mutate((r) => setAt(r, ['season', 'remaining'], -1)), 'season time left out of range'],
    ['season: time left fractional', mutate((r) => setAt(r, ['season', 'remaining'], 12.5)), 'season time left out of range'],
    ['season: time left over the bound', mutate((r) => setAt(r, ['season', 'remaining'], 1_800_001)), 'season time left out of range'],
    ['season: time left a string', mutate((r) => setAt(r, ['season', 'remaining'], '100')), 'season time left out of range'],
    ['season: time left null (NaN serialises to null)', mutate((r) => setAt(r, ['season', 'remaining'], null)), 'season time left out of range'],
    ['weather: not an object', mutate((r) => setAt(r, ['weather'], 'rain')), 'weather keys differ from the contract'],
    ['weather: missing key', mutate((r) => delAt(r, ['weather', 'seed'])), 'weather keys differ from the contract'],
    ['weather: extra key', mutate((r) => setAt(r, ['weather', 'extra'], 1)), 'weather keys differ from the contract'],
    ['weather: unknown mode', mutate((r) => setAt(r, ['weather', 'mode'], 'hail')), 'weather mode unknown'],
    ['weather: a v1 mode', mutate((r) => setAt(r, ['weather', 'mode'], 'snow')), 'weather mode unknown'],
    ['weather: seed negative', mutate((r) => setAt(r, ['weather', 'seed'], -1)), 'weather seed out of range'],
    ['weather: seed fractional', mutate((r) => setAt(r, ['weather', 'seed'], 1.5)), 'weather seed out of range'],
    ['weather: seed over the bound', mutate((r) => setAt(r, ['weather', 'seed'], 4_294_967_296)), 'weather seed out of range'],
    ['weather: regions not an array', mutate((r) => setAt(r, ['weather', 'regions'], {})), 'weather needs exactly 5 regions'],
    ['weather: four regions', mutate((r) => ((parent(r, ['weather', 'regions', 0]) as unknown) as unknown[]).pop()), 'weather needs exactly 5 regions'],
    ['weather: six regions', mutate((r) => ((parent(r, ['weather', 'regions', 0]) as unknown) as unknown[]).push(JSON.parse(JSON.stringify(record().weather.regions[0])))), 'weather needs exactly 5 regions'],
    ['region: not an object', mutate((r) => setAt(r, ['weather', 'regions', 2], 'rain')), 'region keys differ from the contract'],
    ['region: missing key', mutate((r) => delAt(r, ['weather', 'regions', 1, 'wet'])), 'region keys differ from the contract'],
    ['region: extra key', mutate((r) => setAt(r, ['weather', 'regions', 4, 'x'], 1)), 'region keys differ from the contract'],
    ['region: unknown kind', mutate((r) => setAt(r, ['weather', 'regions', 0, 'kind'], 'snow')), 'region weather kind unknown'],
    ['region: step negative', mutate((r) => setAt(r, ['weather', 'regions', 3, 'step'], -1)), 'region draw index out of range'],
    ['region: step over the bound', mutate((r) => setAt(r, ['weather', 'regions', 3, 'step'], 1_000_000)), 'region draw index out of range'],
    ['region: step fractional', mutate((r) => setAt(r, ['weather', 'regions', 3, 'step'], 0.5)), 'region draw index out of range'],
    ['region: wet streak over the bound', mutate((r) => setAt(r, ['weather', 'regions', 0, 'wet'], 3)), 'region wet streak out of range'],
    ['region: time left over the bound', mutate((r) => setAt(r, ['weather', 'regions', 0, 'remaining'], 300_001)), 'region time left out of range'],
    ['region: time left negative', mutate((r) => setAt(r, ['weather', 'regions', 0, 'remaining'], -5)), 'region time left out of range'],
    ['automatic weather: a clear region with a wet streak', mutate((r) => setAt(r, ['weather', 'regions', 2, 'wet'], 1)), 'region wet streak disagrees with its kind'],
    ['automatic weather: a wet region with no wet streak', mutate((r) => { setAt(r, ['weather', 'regions', 1, 'kind'], 'rain'); }), 'region wet streak disagrees with its kind'],
    ['an inherited-looking key', JSON.parse('{"v":2,"lighting":"day","traffic":"off","season":{},"weather":{},"__proto__":{"x":1}}'), 'preference keys differ from the contract'],
  ];
  for (const [what, value, reason] of cases) assert.equal(refused(value), reason, what);
  assert.equal(cases.length, 49); // the list is pinned so it cannot silently shrink
});

test('THE AUTOMATIC STREAK INVARIANT: clear means streak 0 and wet means 1 to the maximum, only while the mode is automatic', () => {
  const withRegion = (mode: string, kind: string, wet: number) => {
    const r = json();
    setAt(r, ['weather', 'mode'], mode);
    setAt(r, ['weather', 'regions', 0, 'kind'], kind);
    setAt(r, ['weather', 'regions', 0, 'wet'], wet);
    return r;
  };
  // automatic: exactly the states the product's draws produce
  assert.equal(refused(withRegion('auto', 'clear', 0)), null);
  for (const kind of ['rain', 'light-snow', 'heavy-snow']) for (const wet of [1, 2]) assert.equal(refused(withRegion('auto', kind, wet)), null, `${kind} ${wet}`);
  assert.equal(refused(withRegion('auto', 'clear', 1)), 'region wet streak disagrees with its kind');
  assert.equal(refused(withRegion('auto', 'clear', 2)), 'region wet streak disagrees with its kind');
  for (const kind of ['rain', 'light-snow', 'heavy-snow']) assert.equal(refused(withRegion('auto', kind, 0)), 'region wet streak disagrees with its kind', kind);
  // manual: a manual choice leaves the streak as the schedule left it, so any streak in range is admissible
  for (const mode of ['clear', 'rain', 'light-snow', 'heavy-snow'])
    for (const kind of ['clear', 'rain'])
      for (const wet of [0, 1, 2]) assert.equal(refused(withRegion(mode, kind, wet)), null, `${mode} ${kind} ${wet}`);
  // the wet streak is bounded by the pinned maximum whatever the mode
  assert.equal(refused(withRegion('rain', 'rain', 3)), 'region wet streak out of range');
});

test('every number is accepted at its bound and refused one past it', () => {
  // `prepare` sets up the rest of the record for a number whose admissible values depend on another field (the wet streak is only free of the
  // kind while the weather is manual: in Automatic weather a clear region's streak is 0)
  const edge = (path: Key[], max: number, prepare: (r: unknown) => void = () => undefined) => {
    for (const ok of [0, max]) {
      const r = json();
      prepare(r);
      setAt(r, path, ok);
      assert.equal(refused(r), null, `${path.join('.')} = ${ok}`);
    }
    const r = json();
    prepare(r);
    setAt(r, path, max + 1);
    assert.notEqual(refused(r), null, `${path.join('.')} = ${max + 1}`);
  };
  edge(['season', 'remaining'], 1_800_000);
  edge(['weather', 'seed'], 4_294_967_295);
  edge(['weather', 'regions', 4, 'step'], 999_999);
  edge(['weather', 'regions', 4, 'wet'], 2, (r) => setAt(r, ['weather', 'mode'], 'rain'));
  edge(['weather', 'regions', 4, 'remaining'], 300_000);
});

/** The size of the longest legal stored record, derived by hand in the golden below, and of the shortest (every code and number 0). */
const WORST_STORED = 160;
const SMALLEST_STORED = 95;
const WORST_GOLDEN =
  '{"v":2,"l":3,"t":2,"s":[1,3,1,1800000],"w":[4,4294967295,[[3,999999,2,300000],[3,999999,2,300000],[3,999999,2,300000],[3,999999,2,300000],[3,999999,2,300000]]]}';

test('THE BYTE CEILING: the longest legal STORED record is exactly 160 bytes against the 256-byte budget; at its own size it is accepted and one byte under it refused', () => {
  const worst = record();
  worst.lighting = 'clock'; // the last code of the list
  worst.traffic = 'normal';
  worst.season = { mode: 'manual', season: 'winter', paused: true, remaining: 1_800_000 };
  worst.weather = {
    mode: 'heavy-snow',
    seed: 4_294_967_295,
    regions: [0, 1, 2, 3, 4].map(() => ({ kind: 'heavy-snow' as const, step: 999_999, wet: 2, remaining: 300_000 })),
  };
  assert.equal(core.checkCityPresentationV2(worst), null);
  // the golden is written out by hand (one code per enumerated value, the longest number everywhere); the code agrees with it byte for byte
  assert.equal(WORST_GOLDEN.length, WORST_STORED);
  assert.equal(encoded(worst), WORST_GOLDEN);
  assert.ok(WORST_STORED <= core.CITY_PRESENTATION_V2_MAX_BYTES);
  assert.equal(core.CITY_PRESENTATION_V2_MAX_BYTES - WORST_STORED, 96, 'the margin under the ceiling');
  // the named (verbose) record could NOT keep the 256-byte budget; that is why the server stores the compact form
  assert.equal(core.cityPresentationBytes(worst), 501);
  // the longest named record has the flag spelled `false` (one more character); the stored form spends one digit on it either way
  const longestNamed = { ...worst, season: { ...worst.season, paused: false } };
  assert.equal(core.cityPresentationBytes(longestNamed), 502);
  assert.equal(encoded(longestNamed).length, WORST_STORED);
  assert.equal(core.cityPresentationBytes(core.defaultCityPresentationV2()), 412);
  assert.ok(502 > core.CITY_PRESENTATION_V2_MAX_BYTES);
  // AT the size: accepted. ONE BYTE UNDER it: refused, with the reason. (The ceiling is a parameter of the real function.)
  assert.deepEqual(core.encodeStoredCityPresentationV2(worst, WORST_STORED), { ok: true, text: WORST_GOLDEN });
  assert.deepEqual(core.encodeStoredCityPresentationV2(worst, WORST_STORED - 1), { ok: false, reason: 'preferences over 159 bytes' });
  // and at the real ceiling a record is accepted up to 256 bytes and no byte more
  assert.equal(core.encodeStoredCityPresentationV2(worst, 256).ok, true);
  assert.equal(core.encodeStoredCityPresentationV2(worst, 0).ok, false);
});

test('THE STORED FORM is exactly this, written by hand: the default city, a varied city and the longest city (the code tables cannot be reordered silently)', () => {
  assert.equal(
    encoded(core.defaultCityPresentationV2()),
    '{"v":2,"l":0,"t":2,"s":[0,1,0,1800000],"w":[0,20903,[[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]]]}',
  );
  const varied: core.CityPresentationV2 = {
    v: 2,
    lighting: 'dusk',
    traffic: 'low',
    season: { mode: 'manual', season: 'winter', paused: true, remaining: 123_456 },
    weather: {
      mode: 'heavy-snow',
      seed: 4242,
      regions: [
        { kind: 'clear', step: 0, wet: 0, remaining: 0 },
        { kind: 'rain', step: 1, wet: 1, remaining: 100_000 },
        { kind: 'light-snow', step: 2, wet: 2, remaining: 200_000 },
        { kind: 'heavy-snow', step: 3, wet: 0, remaining: 300_000 },
        { kind: 'clear', step: 999_999, wet: 1, remaining: 5 },
      ],
    },
  };
  const text = '{"v":2,"l":1,"t":1,"s":[1,3,1,123456],"w":[4,4242,[[0,0,0,0],[1,1,1,100000],[2,2,2,200000],[3,3,0,300000],[0,999999,1,5]]]}';
  assert.equal(core.checkCityPresentationV2(varied), null);
  assert.equal(encoded(varied), text);
  assert.deepEqual(unpacked(text), varied);
  // every code of every list, in every position
  const lists: Array<[string, readonly string[]]> = [
    ['lighting', ['day', 'dusk', 'night', 'clock']],
    ['traffic', ['off', 'low', 'normal']],
    ['season mode', ['auto', 'manual']],
    ['season', ['spring', 'summer', 'autumn', 'winter']],
    ['weather mode', ['auto', 'clear', 'rain', 'light-snow', 'heavy-snow']],
    ['region kind', ['clear', 'rain', 'light-snow', 'heavy-snow']],
  ];
  assert.deepEqual(lists[0][1], core.CITY_LIGHTING_MODES);
  assert.deepEqual(lists[1][1], core.CITY_TRAFFIC_MODES);
  assert.deepEqual(lists[2][1], core.CITY_SEASON_MODES);
  assert.deepEqual(lists[3][1], core.CITY_SEASON_NAMES);
  assert.deepEqual(lists[4][1], core.CITY_WEATHER_V2_MODES);
  assert.deepEqual(lists[5][1], core.CITY_WEATHER_KINDS);
  // every kind in every slot, every wet streak, every code survives the round trip on its own
  for (let slot = 0; slot < 5; slot++)
    for (const kind of core.CITY_WEATHER_KINDS)
      for (const wet of [0, 1, 2]) {
        const r = record();
        r.weather = { ...r.weather, mode: 'rain', regions: r.weather.regions.map((x, i) => (i === slot ? { ...x, kind, wet, step: slot + 7 } : x)) };
        assert.deepEqual(unpacked(encoded(r)), r, `${kind} in slot ${slot} with streak ${wet}`);
      }
});

test('A STORED RECORD that is malformed is refused whole with a reason, at every level; the named record is never accepted as stored', () => {
  const stored = (): unknown => JSON.parse(encoded(core.defaultCityPresentationV2()));
  const mutate = (change: (r: Record<string, unknown>) => void): unknown => {
    const r = stored() as Record<string, unknown>;
    change(r);
    return r;
  };
  const at = (r: Record<string, unknown>, key: string): unknown[] => r[key] as unknown[];
  const cases: Array<[string, unknown, string]> = [
    ['not an object', 5, 'stored preference keys differ from the contract'],
    ['an array', [], 'stored preference keys differ from the contract'],
    ['null', null, 'stored preference keys differ from the contract'],
    ['the NAMED record is not a stored record', core.defaultCityPresentationV2(), 'stored preference keys differ from the contract'],
    ['an extra key', mutate((r) => (r.x = 1)), 'stored preference keys differ from the contract'],
    ['a missing key', mutate((r) => delete r.t), 'stored preference keys differ from the contract'],
    ['an unknown version', mutate((r) => (r.v = 3)), 'preference version unsupported'],
    ['lighting code past the list', mutate((r) => (r.l = 4)), 'lighting mode unknown'],
    ['lighting code negative', mutate((r) => (r.l = -1)), 'lighting mode unknown'],
    ['lighting code fractional', mutate((r) => (r.l = 1.5)), 'lighting mode unknown'],
    ['lighting as a word', mutate((r) => (r.l = 'night')), 'lighting mode unknown'],
    ['traffic code past the list', mutate((r) => (r.t = 3)), 'traffic mode unknown'],
    ['traffic null', mutate((r) => (r.t = null)), 'traffic mode unknown'],
    ['season fields: three', mutate((r) => at(r, 's').pop()), 'stored season needs exactly 4 fields'],
    ['season fields: five', mutate((r) => at(r, 's').push(0)), 'stored season needs exactly 4 fields'],
    ['season not an array', mutate((r) => (r.s = {})), 'stored season needs exactly 4 fields'],
    ['season mode code past the list', mutate((r) => (at(r, 's')[0] = 2)), 'season mode unknown'],
    ['season code past the list', mutate((r) => (at(r, 's')[1] = 4)), 'season unknown'],
    ['pause flag 2', mutate((r) => (at(r, 's')[2] = 2)), 'season pause flag is not 0 or 1'],
    ['pause flag true', mutate((r) => (at(r, 's')[2] = true)), 'season pause flag is not 0 or 1'],
    ['season time left one past the range', mutate((r) => (at(r, 's')[3] = 1_800_001)), 'season time left out of range'],
    ['season time left a string', mutate((r) => (at(r, 's')[3] = '5')), 'season time left out of range'],
    ['weather: two fields', mutate((r) => at(r, 'w').pop()), 'stored weather needs a mode, a seed and a region list'],
    ['weather: four fields', mutate((r) => at(r, 'w').push(0)), 'stored weather needs a mode, a seed and a region list'],
    ['weather: regions not a list', mutate((r) => (at(r, 'w')[2] = {})), 'stored weather needs a mode, a seed and a region list'],
    ['weather mode code past the list', mutate((r) => (at(r, 'w')[0] = 5)), 'weather mode unknown'],
    ['seed one past the range', mutate((r) => (at(r, 'w')[1] = 4_294_967_296)), 'weather seed out of range'],
    ['seed negative', mutate((r) => (at(r, 'w')[1] = -1)), 'weather seed out of range'],
    ['four regions', mutate((r) => (at(r, 'w')[2] as unknown[]).pop()), 'weather needs exactly 5 regions'],
    ['six regions', mutate((r) => (at(r, 'w')[2] as unknown[]).push([0, 0, 0, 0])), 'weather needs exactly 5 regions'],
    ['a region of three fields', mutate((r) => (at(r, 'w')[2] as unknown[][])[2].pop()), 'stored region needs exactly 4 fields'],
    ['a region of five fields', mutate((r) => (at(r, 'w')[2] as unknown[][])[2].push(0)), 'stored region needs exactly 4 fields'],
    ['a region that is not a list', mutate((r) => ((at(r, 'w')[2] as unknown[])[0] = {})), 'stored region needs exactly 4 fields'],
    ['region kind code past the list', mutate((r) => ((at(r, 'w')[2] as unknown[][])[1][0] = 4)), 'region weather kind unknown'],
    ['region draw index one past the range', mutate((r) => ((at(r, 'w')[2] as unknown[][])[1][1] = 1_000_000)), 'region draw index out of range'],
    ['region wet streak past the maximum', mutate((r) => ((at(r, 'w')[2] as unknown[][])[1][2] = 3)), 'region wet streak out of range'],
    ['region time left one past the range', mutate((r) => ((at(r, 'w')[2] as unknown[][])[1][3] = 300_001)), 'region time left out of range'],
    ['an automatic clear region with a wet streak', mutate((r) => ((at(r, 'w')[2] as unknown[][])[1][2] = 1)), 'region wet streak disagrees with its kind'],
  ];
  for (const [what, value, reason] of cases) {
    const m = core.unpackCityPresentationV2(value);
    assert.deepEqual(m, { ok: false, reason }, what);
  }
  assert.ok(cases.length >= 38);
  // the stored record that is valid is accepted (the control for every case above)
  assert.equal(core.unpackCityPresentationV2(stored()).ok, true);
});

test('READING FROM STORAGE: a stored v2 record is unpacked, a v1 record goes through THE ONE MIGRATION, anything else is refused whole', () => {
  const v1 = { v: 1, lighting: 'night', weather: 'snow', traffic: 'low' };
  const fromV1 = core.readStoredCityPresentation(v1);
  assert.ok(fromV1.ok);
  if (fromV1.ok) {
    assert.equal(fromV1.from, 1);
    assert.deepEqual(fromV1.state, (core.migrateCityPresentation(v1) as { state: unknown }).state, 'the very same migration');
  }
  const fromV2 = core.readStoredCityPresentation(JSON.parse(WORST_GOLDEN));
  assert.ok(fromV2.ok && fromV2.from === 2);
  for (const raw of [record(), { v: 3 }, { v: 0 }, 'x', null, []])
    assert.equal(core.readStoredCityPresentation(raw).ok, false, JSON.stringify(raw)?.slice(0, 40));
  // the NAMED v2 record is not stored form: a file holding it is refused (it could not fit the budget anyway)
  assert.deepEqual(core.readStoredCityPresentation(record()), { ok: false, reason: 'stored preference keys differ from the contract' });
});

test('MIGRATION of a valid v2 record: returned as it is, from 2', () => {
  const r = record();
  r.lighting = 'dusk';
  r.season = { mode: 'manual', season: 'winter', paused: true, remaining: 123_456 };
  const m = core.migrateCityPresentation(r);
  assert.ok(m.ok);
  if (m.ok) {
    assert.equal(m.from, 2);
    assert.deepEqual(m.state, r);
    assert.ok(m.state === r, 'the very same object: a v2 record is not rebuilt');
  }
});

test('MIGRATION of EVERY valid v1 record: lighting and traffic kept, weather off/rain/snow become the manual Clear/Rain/Light snow, seasons Automatic from summer with a full interval', () => {
  const expectedMode = { off: 'clear', rain: 'rain', snow: 'light-snow' } as const;
  let n = 0;
  for (const lighting of core.CITY_LIGHTING_MODES) {
    for (const weather of core.CITY_WEATHER_MODES) {
      for (const traffic of core.CITY_TRAFFIC_MODES) {
        const v1 = { v: 1, lighting, weather, traffic };
        const input = JSON.stringify(v1);
        const m = core.migrateCityPresentation(v1);
        assert.ok(m.ok, input);
        if (!m.ok) continue;
        assert.equal(m.from, 1);
        assert.equal(core.checkCityPresentationV2(m.state), null, 'the migrated record is a valid v2 record');
        assert.equal(m.state.lighting, lighting);
        assert.equal(m.state.traffic, traffic);
        assert.equal(m.state.weather.mode, expectedMode[weather], `weather ${weather}`);
        assert.deepEqual(m.state.season, { mode: 'auto', season: 'summer', paused: false, remaining: 1_800_000 });
        assert.equal(m.state.weather.seed, 0x51a7);
        assert.deepEqual(m.state.weather.regions, [0, 1, 2, 3, 4].map(() => ({ kind: 'clear', step: 0, wet: 0, remaining: 0 })));
        assert.equal(JSON.stringify(v1), input, 'the input was not touched');
        // migrating what was migrated changes nothing (and it is now "from 2")
        const again = core.migrateCityPresentation(m.state);
        assert.ok(again.ok && again.from === 2 && JSON.stringify(again.state) === JSON.stringify(m.state));
        n++;
      }
    }
  }
  assert.equal(n, 4 * 3 * 3);
  assert.equal(core.migratedWeatherMode('off'), 'clear');
  assert.equal(core.migratedWeatherMode('rain'), 'rain');
  assert.equal(core.migratedWeatherMode('snow'), 'light-snow');
});

test('MIGRATION refuses everything else whole, with the reason, and returns nothing to apply', () => {
  const bad: Array<[string, unknown]> = [
    ['null', null],
    ['an array', []],
    ['a string', 'x'],
    ['a v1 record with an extra key', { v: 1, lighting: 'day', weather: 'off', traffic: 'off', extra: 1 }],
    ['a v1 record with an unknown weather', { v: 1, lighting: 'day', weather: 'hail', traffic: 'off' }],
    ['a v1 record with a missing key', { v: 1, lighting: 'day', weather: 'off' }],
    ['an unknown version', { v: 3, lighting: 'day', weather: 'off', traffic: 'off' }],
    ['a v2 record with a bad nested value', { ...record(), season: { ...record().season, remaining: -1 } }],
    ['a v2 record with four regions', { ...record(), weather: { ...record().weather, regions: record().weather.regions.slice(1) } }],
    ['an empty object', {}],
  ];
  for (const [what, value] of bad) {
    const m = core.migrateCityPresentation(value);
    assert.equal(m.ok, false, what);
    if (!m.ok) assert.equal(typeof m.reason, 'string', what);
    assert.ok(!('state' in m), `${what}: nothing is partly kept`);
  }
  // the reason names what is wrong
  const nested = core.migrateCityPresentation({ ...record(), season: { ...record().season, remaining: -1 } });
  assert.deepEqual(nested, { ok: false, reason: 'season time left out of range' });
});

test('ONE migration function: the server and the webview use it, and neither carries a mapping of its own', () => {
  const root = path.join(__dirname, '..', '..');
  const server = fs.readFileSync(path.join(root, 'server/src/di/cityPresentationPersistence.ts'), 'utf-8');
  const hook = fs.readFileSync(path.join(root, 'webview-ui/src/hooks/useCityPresentation.ts'), 'utf-8');
  const effects = fs.readFileSync(path.join(root, 'webview-ui/src/office/city/cityEffects.ts'), 'utf-8');
  const coreSrc = fs.readFileSync(path.join(root, 'core/src/cityPresentation.ts'), 'utf-8');
  // the webview applies a record through the migration itself; the server reads a stored file through the shared reader, which calls the
  // very same migration for a v1 record (and unpacks a stored v2 one)
  assert.ok(/migrateCityPresentation/.test(hook), 'webview hook uses migrateCityPresentation');
  assert.ok(/readStoredCityPresentation/.test(server) && !/migrateCityPresentation/.test(server), 'server persistence reads through the shared reader');
  const reader = coreSrc.slice(coreSrc.indexOf('export function readStoredCityPresentation'));
  assert.ok(/migrateCityPresentation\(raw\)/.test(reader), 'the shared reader calls the one migration for a v1 record');
  // the v1 -> v2 weather mapping exists once (core); the webview's helper delegates to it
  assert.equal((coreSrc.match(/'light-snow'/g) ?? []).length >= 2, true);
  assert.ok(/migratedWeatherMode\(weather\)/.test(effects) && !/'light-snow'/.test(effects.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')), 'legacyWeatherMode delegates to the core mapping');
  assert.ok(!/weather === 'snow'/.test(server) && !/weather === 'snow'/.test(hook), 'no second mapping in the server or the hook');
});
