/**
 * DI office: the viewer's CITY PRESENTATION PREFERENCES (living city 6–9) — the same four kinds of test the
 * ambient snapshot has, for a new persisted file: ADMISSION (only the contract shape is written),
 * PERSISTENCE (written atomically under the injected root, read back identical, sent in the handshake
 * before the layout), SECURITY (malformed, oversized or hand-planted files are refused or ignored; nothing
 * lands outside the root), RESOURCE (the byte ceiling holds for every admissible choice).
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  checkCityPresentation,
  CITY_LIGHTING_MODES,
  CITY_PRESENTATION_MAX_BYTES,
  CITY_TRAFFIC_MODES,
  CITY_WEATHER_MODES,
  cityPresentationBytes,
} from '../../core/src/cityPresentation.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { CITY_PRESENTATION_FILE_NAME } from '../src/constants.js';
import {
  readCityPresentation,
  writeCityPresentation,
} from '../src/di/cityPresentationPersistence.js';
import {
  DI_HANDLED_MESSAGE_TYPES,
  DI_INERT_MESSAGE_TYPES,
  handleDiClientMessage,
} from '../src/di/diClientMessages.js';
import { resetStateRootForTests, setStateRoot } from '../src/stateRoot.js';

let sandbox: string;
let root: string;
const EMPTY_CACHE = {
  characters: null,
  pets: null,
  floorTiles: null,
  wallTiles: null,
  carpetTiles: null,
  furniture: null,
  defaultLayout: null,
};
const choice = { v: 1, lighting: 'night', weather: 'rain', traffic: 'low' };
const ctx = () => ({
  store: new AgentStateStore(),
  cache: EMPTY_CACHE as never,
  extensionVersion: 'x',
});
const handshake = () => {
  const sent: Array<Record<string, unknown>> = [];
  handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), ctx());
  return sent;
};

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-city-')));
  root = path.join(sandbox, 'di-root');
  resetStateRootForTests();
  setStateRoot(root);
});
afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('admission', () => {
  it('the contract admits every choice and refuses every other shape', () => {
    for (const lighting of CITY_LIGHTING_MODES)
      for (const weather of CITY_WEATHER_MODES)
        for (const traffic of CITY_TRAFFIC_MODES)
          expect(checkCityPresentation({ v: 1, lighting, weather, traffic })).toBeNull();
    const bad: Array<[string, unknown]> = [
      ['not an object', 'x'],
      ['an array', [choice]],
      ['an extra key', { ...choice, savedAt: 1 }],
      ['a missing key', { v: 1, lighting: 'night', weather: 'rain' }],
      ['another version', { ...choice, v: 2 }],
      ['an unknown lighting mode', { ...choice, lighting: 'noon' }],
      ['an unknown weather mode', { ...choice, weather: 'hail' }],
      ['an unknown traffic mode', { ...choice, traffic: 'jam' }],
      ['a non-string mode', { ...choice, traffic: 3 }],
    ];
    for (const [name, value] of bad) expect(checkCityPresentation(value), name).not.toBeNull();
  });
});

describe('persistence', () => {
  it('saveCityPresentation writes the choice under the injected root, atomically, and it reads back identical', () => {
    const sent: Array<Record<string, unknown>> = [];
    const out = handleDiClientMessage(
      { type: 'saveCityPresentation', state: choice },
      (m) => sent.push(m),
      ctx(),
    );
    expect(out).toBe('handled');
    expect(fs.existsSync(path.join(root, CITY_PRESENTATION_FILE_NAME))).toBe(true);
    expect(fs.existsSync(path.join(root, CITY_PRESENTATION_FILE_NAME + '.tmp'))).toBe(false);
    expect(readCityPresentation()).toEqual(choice);
    expect(sent).toEqual([]); // a save is not answered
  });

  it('a later choice replaces the earlier one', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    const later = { v: 1, lighting: 'clock', weather: 'snow', traffic: 'off' };
    expect(writeCityPresentation(later)).toBeNull();
    expect(readCityPresentation()).toEqual(later);
  });

  it('the handshake sends the saved choice BEFORE the layout, and null when none was saved', () => {
    let sent = handshake();
    expect(sent.find((m) => m.type === 'cityPresentationLoaded')).toEqual({
      type: 'cityPresentationLoaded',
      state: null,
    });
    expect(writeCityPresentation(choice)).toBeNull();
    sent = handshake();
    const types = sent.map((m) => m.type);
    expect(types.indexOf('cityPresentationLoaded')).toBeGreaterThan(-1);
    expect(types.indexOf('cityPresentationLoaded')).toBeLessThan(types.indexOf('layoutLoaded'));
    expect(sent.find((m) => m.type === 'cityPresentationLoaded')).toEqual({
      type: 'cityPresentationLoaded',
      state: choice,
    });
  });

  it('the message is one the DI office handles, and not one it ignores', () => {
    expect(DI_HANDLED_MESSAGE_TYPES).toContain('saveCityPresentation');
    expect(DI_INERT_MESSAGE_TYPES as readonly string[]).not.toContain('saveCityPresentation');
  });
});

describe('security', () => {
  it('a refused save writes nothing; a planted malformed, oversized or foreign file is ignored on read', () => {
    const out = handleDiClientMessage(
      { type: 'saveCityPresentation', state: { ...choice, lighting: 'noon' } },
      () => {},
      ctx(),
    );
    expect(out).toEqual({ refused: 'lighting mode unknown' });
    expect(fs.existsSync(path.join(root, CITY_PRESENTATION_FILE_NAME))).toBe(false);
    fs.mkdirSync(root, { recursive: true });
    const file = path.join(root, CITY_PRESENTATION_FILE_NAME);
    for (const planted of [
      '{"v":1,"lighting":"night"}',
      'x'.repeat(CITY_PRESENTATION_MAX_BYTES + 1),
      'not json',
      '[]',
      JSON.stringify({ ...choice, extra: 1 }),
    ]) {
      fs.writeFileSync(file, planted);
      expect(readCityPresentation(), planted.slice(0, 30)).toBeNull();
    }
    // a directory where the file belongs is not a file
    fs.rmSync(file);
    fs.mkdirSync(file);
    expect(readCityPresentation()).toBeNull();
  });

  it('only the preference file is created, inside the root', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    expect(fs.readdirSync(root)).toEqual([CITY_PRESENTATION_FILE_NAME]);
  });

  it('a saved choice carries no Mission, agent or position: it is exactly the four contract keys', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    expect(
      Object.keys(
        JSON.parse(fs.readFileSync(path.join(root, CITY_PRESENTATION_FILE_NAME), 'utf-8')),
      ).sort(),
    ).toEqual(['lighting', 'traffic', 'v', 'weather']);
  });
});

describe('resource', () => {
  it('every admissible choice fits the ceiling, and the range is measured exactly', () => {
    const sizes: number[] = [];
    for (const lighting of CITY_LIGHTING_MODES)
      for (const weather of CITY_WEATHER_MODES)
        for (const traffic of CITY_TRAFFIC_MODES)
          sizes.push(cityPresentationBytes({ v: 1, lighting, weather, traffic }));
    expect(sizes).toHaveLength(36);
    expect(Math.min(...sizes)).toBe(56);
    expect(Math.max(...sizes)).toBe(62);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(CITY_PRESENTATION_MAX_BYTES);
  });

  it('the ceiling is exactly 256 bytes and is defence in depth: the shape check refuses first, and no admissible shape is near it', () => {
    expect(CITY_PRESENTATION_MAX_BYTES).toBe(256);
    // padded text is refused as an unknown mode, never reaching the size check
    expect(checkCityPresentation({ ...choice, lighting: 'night'.padEnd(300, 'x') })).toBe(
      'lighting mode unknown',
    );
    // the largest admissible record is a quarter of the ceiling
    expect(62 * 4).toBeLessThanOrEqual(CITY_PRESENTATION_MAX_BYTES);
  });
});
