/**
 * LIVING CITY 6–9, the presentation contract and EVERY new TypeScript bound, asserted exactly.
 *
 * Why this file exists as it does: tests/test_workflow_authority.py (BoundConstantPinTests) walks product
 * PYTHON files only and has no webview-ui entries, so a TypeScript bound cannot be pinned there. A bound
 * that is not asserted here can drift silently, so each one has an explicit exact-value assertion.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import * as core from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';

test('the presentation contract is exactly these values', () => {
  assert.equal(core.CITY_PRESENTATION_VERSION, 1);
  assert.equal(core.CITY_PRESENTATION_MAX_BYTES, 256);
  assert.deepEqual([...core.CITY_LIGHTING_MODES], ['day', 'dusk', 'night', 'clock']);
  assert.deepEqual([...core.CITY_WEATHER_MODES], ['off', 'rain', 'snow']);
  assert.deepEqual([...core.CITY_TRAFFIC_MODES], ['off', 'low', 'normal']);
  // what a viewer with no saved choice sees is the city as it always looked
  assert.deepEqual(
    { ...core.DEFAULT_CITY_PRESENTATION },
    { v: 1, lighting: 'day', weather: 'off', traffic: 'normal' },
  );
  assert.ok(Object.isFrozen(core.DEFAULT_CITY_PRESENTATION));
});

test('every admissible choice is accepted and fits the byte ceiling', () => {
  let n = 0;
  for (const lighting of core.CITY_LIGHTING_MODES) {
    for (const weather of core.CITY_WEATHER_MODES) {
      for (const traffic of core.CITY_TRAFFIC_MODES) {
        const record = { v: 1, lighting, weather, traffic };
        assert.equal(core.checkCityPresentation(record), null, JSON.stringify(record));
        assert.ok(core.cityPresentationBytes(record) <= core.CITY_PRESENTATION_MAX_BYTES);
        n++;
      }
    }
  }
  assert.equal(n, 4 * 3 * 3);
  assert.equal(core.checkCityPresentation(core.DEFAULT_CITY_PRESENTATION), null);
});

test('every malformed shape is refused with a reason and nothing is half-accepted', () => {
  const ok = { v: 1, lighting: 'night', weather: 'rain', traffic: 'low' };
  const refused: Array<[string, unknown]> = [
    ['null', null],
    ['an array', [ok]],
    ['a string', JSON.stringify(ok)],
    ['a number', 7],
    ['no keys', {}],
    ['a missing key', { v: 1, lighting: 'night', weather: 'rain' }],
    ['an extra key', { ...ok, seed: 1 }],
    ['an extra key that carries a position', { ...ok, x: 3 }],
    ['the wrong version', { ...ok, v: 2 }],
    ['a string version', { ...ok, v: '1' }],
    ['an unknown lighting mode', { ...ok, lighting: 'noon' }],
    ['an unknown weather mode', { ...ok, weather: 'hail' }],
    ['an unknown traffic mode', { ...ok, traffic: 'gridlock' }],
    ['a non-string mode', { ...ok, weather: 1 }],
    [
      'an inherited-looking key',
      JSON.parse('{"v":1,"lighting":"day","weather":"off","traffic":"off","__proto__":{"x":1}}'),
    ],
  ];
  for (const [what, value] of refused) {
    const reason = core.checkCityPresentation(value);
    assert.equal(typeof reason, 'string', `${what} must be refused`);
  }
});

test('the byte measure counts the real serialization and is Infinity for what cannot serialize', () => {
  assert.equal(core.cityPresentationBytes({ a: 1 }), '{"a":1}'.length);
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  assert.equal(core.cityPresentationBytes(cyclic), Infinity);
});

test('lighting bounds are exactly these values', () => {
  assert.deepEqual({ ...C.CITY_LIGHT_STAGE_DAY }, { r: 0, g: 0, b: 0, a: 0 });
  assert.deepEqual({ ...C.CITY_LIGHT_STAGE_DUSK }, { r: 74, g: 38, b: 66, a: 0.3 });
  assert.deepEqual({ ...C.CITY_LIGHT_STAGE_NIGHT }, { r: 8, g: 14, b: 46, a: 0.58 });
  assert.equal(C.CITY_LIGHT_LEVEL_DUSK, 0.5);
  assert.equal(C.CITY_LIGHT_LEVEL_NIGHT, 1);
  assert.equal(C.CITY_LIGHT_VISIBLE_EPSILON, 0.01);
  assert.equal(C.CITY_LIGHT_FADE_SEC, 0.8);
  assert.equal(C.CITY_LIGHT_INDOOR_LIT, 0.85);
  assert.equal(C.CITY_LIGHT_AGENT_RADIUS_TILES, 3);
  assert.equal(C.CITY_LIGHT_LAMP_RADIUS_TILES, 4);
  assert.equal(C.CITY_LIGHT_AGENT_STRENGTH, 0.95);
  assert.equal(C.CITY_LIGHT_LAMP_STRENGTH, 0.9);
  assert.equal(C.CITY_LIGHT_MAX_LIGHTS, 96);
  assert.equal(C.CITY_LIGHT_AGENT_LIFT_PX, 4);
  assert.equal(C.CITY_LIGHT_VIEW_MARGIN_TILES, 5);
  assert.equal(C.CITY_LIGHT_SPRITE_PX, 64);
  assert.deepEqual({ ...C.CITY_LIGHT_LAMP_GLOW }, { r: 255, g: 196, b: 110 });
  assert.equal(C.CITY_LIGHT_LAMP_GLOW_ALPHA, 0.22);
  assert.deepEqual(
    [...C.CITY_LIGHT_SOURCE_TYPES],
    ['DETAIL_LAMP_ORNATE', 'DETAIL_LAMP_MODERN', 'DECK_LANTERN'],
  );
  assert.deepEqual(
    [
      C.CITY_CLOCK_DAWN_START_H,
      C.CITY_CLOCK_DAY_START_H,
      C.CITY_CLOCK_DUSK_START_H,
      C.CITY_CLOCK_NIGHT_START_H,
    ],
    [5, 7, 17, 21],
  );
});

test('weather bounds are exactly these values', () => {
  assert.equal(C.CITY_WEATHER_SEED, 0x2c17);
  assert.equal(C.CITY_RAIN_PARTICLES, 520);
  assert.equal(C.CITY_SNOW_PARTICLES, 300);
  assert.equal(C.CITY_RAIN_SPEED, 1.1);
  assert.equal(C.CITY_RAIN_DRIFT, 0.12);
  assert.equal(C.CITY_SNOW_SPEED, 0.07);
  assert.equal(C.CITY_SNOW_SWAY, 0.015);
  assert.equal(C.CITY_RAIN_LENGTH_PX, 6);
  assert.equal(C.CITY_SNOW_SIZE_PX, 1);
  assert.equal(C.CITY_RAIN_COLOR, C.cityRgba(184, 214, 255, 0.72));
  assert.equal(C.CITY_SNOW_COLOR, C.cityRgba(245, 248, 255, 0.9));
  assert.deepEqual({ ...C.CITY_OVERCAST_RAIN }, { r: 62, g: 78, b: 104, a: 0.2 });
  assert.deepEqual({ ...C.CITY_OVERCAST_SNOW }, { r: 120, g: 134, b: 160, a: 0.14 });
});

test('the colour formatter keeps the literal in constants.ts and formats channels exactly', () => {
  assert.match(C.cityRgba(8, 14, 46, 0.58), /^rgba\(8, 14, 46, 0\.58\)$/);
  assert.match(C.cityRgba(0, 0, 0, 0.85), /^rgba\(0, 0, 0, 0\.85\)$/);
});
