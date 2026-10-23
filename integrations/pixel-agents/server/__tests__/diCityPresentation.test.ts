/**
 * DI office: the viewer's CITY PRESENTATION (living city 6–9, item 18; contract v2) — the same four kinds of test the
 * ambient snapshot has, for a persisted file: ADMISSION (only the strict v2 shape is written), PERSISTENCE (written
 * atomically under the injected root, read back identical, sent in the handshake before the layout), SECURITY (malformed,
 * oversized or hand-planted files are refused or ignored; nothing lands outside the root; no other store is touched), RESOURCE
 * (the byte ceiling holds for every admissible record) — and MIGRATION: a saved v1 file is read as v2 (lighting and traffic kept,
 * its weather the same MANUAL choice, seasons Automatic from summer), a read writes nothing, and anything else is ignored whole.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  checkCityPresentationV2,
  CITY_LIGHTING_MODES,
  CITY_PRESENTATION_V2_MAX_BYTES,
  CITY_SEASON_MODES,
  CITY_SEASON_NAMES,
  CITY_TRAFFIC_MODES,
  CITY_WEATHER_KINDS,
  CITY_WEATHER_MODES,
  CITY_WEATHER_V2_MODES,
  defaultCityPresentationV2,
  encodeStoredCityPresentationV2,
  migrateCityPresentation,
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
/** A v2 record that is not the default: night, traffic low, a held winter, Heavy snow, and five regions with their own schedule state. */
const choice = (() => {
  const r = defaultCityPresentationV2();
  r.lighting = 'night';
  r.traffic = 'low';
  r.season = { mode: 'manual', season: 'winter', paused: false, remaining: 1_234_567 };
  r.weather = {
    mode: 'heavy-snow',
    seed: 20_903,
    regions: [
      { kind: 'rain', step: 3, wet: 1, remaining: 123_456 },
      { kind: 'clear', step: 9, wet: 0, remaining: 200_000 },
      { kind: 'light-snow', step: 14, wet: 2, remaining: 5 },
      { kind: 'heavy-snow', step: 999_999, wet: 1, remaining: 300_000 },
      { kind: 'clear', step: 0, wet: 0, remaining: 0 },
    ],
  };
  return r;
})();
/**
 * `choice` as the server STORES it, written out by hand (every enumerated value as its index in the contract's list, the schedule state packed
 * per region): night 2, low 1, manual 1, winter 3, not paused 0; heavy-snow 4; rain 1, clear 0, light-snow 2, heavy-snow 3.
 */
const CHOICE_STORED =
  '{"v":2,"l":2,"t":1,"s":[1,3,0,1234567],"w":[4,20903,[[1,3,1,123456],[0,9,0,200000],[2,14,2,5],[3,999999,1,300000],[0,0,0,0]]]}';
/** The stored record as an object, to break one field at a time. */
const storedObject = () =>
  JSON.parse(CHOICE_STORED) as { s: number[]; w: [number, number, number[][]] } & Record<
    string,
    unknown
  >;
/** The documented budget of the saved city choices (docs/pixel-agents-office.md: 'Saved city choices | 256 bytes'), the v1 cap. */
const BUDGET_BYTES = 256;
/** A saved v1 record, as the previous version of the office wrote it. */
const v1 = { v: 1, lighting: 'night', weather: 'rain', traffic: 'low' };
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
const fileOf = () => path.join(root, CITY_PRESENTATION_FILE_NAME);

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
      for (const traffic of CITY_TRAFFIC_MODES)
        for (const mode of CITY_WEATHER_V2_MODES) {
          const r = defaultCityPresentationV2();
          r.lighting = lighting;
          r.traffic = traffic;
          r.weather.mode = mode;
          expect(checkCityPresentationV2(r)).toBeNull();
        }
    const bad: Array<[string, unknown]> = [
      ['not an object', 'x'],
      ['an array', [choice]],
      ['an extra key', { ...choice, savedAt: 1 }],
      ['a missing key', { ...choice, traffic: undefined }],
      ['another version', { ...choice, v: 3 }],
      ['an unknown lighting mode', { ...choice, lighting: 'noon' }],
      ['an unknown traffic mode', { ...choice, traffic: 'jam' }],
      ['a non-string mode', { ...choice, traffic: 3 }],
      ['an unknown season', { ...choice, season: { ...choice.season, season: 'monsoon' } }],
      [
        'a season time left over the bound',
        { ...choice, season: { ...choice.season, remaining: 1_800_001 } },
      ],
      [
        'four regions',
        { ...choice, weather: { ...choice.weather, regions: choice.weather.regions.slice(1) } },
      ],
      [
        'a region step over the bound',
        {
          ...choice,
          weather: {
            ...choice.weather,
            regions: choice.weather.regions.map((r) => ({ ...r, step: 1_000_000 })),
          },
        },
      ],
      ['a v1 record (only v2 is written)', v1],
      [
        'automatic weather with a clear region that has a wet streak',
        {
          ...choice,
          weather: {
            ...choice.weather,
            mode: 'auto',
            regions: choice.weather.regions.map((r) => ({ ...r, kind: 'clear' as const, wet: 1 })),
          },
        },
      ],
    ];
    for (const [name, value] of bad) expect(checkCityPresentationV2(value), name).not.toBeNull();
    expect(CITY_SEASON_MODES).toHaveLength(2);
    expect(CITY_SEASON_NAMES).toHaveLength(4);
    expect(CITY_WEATHER_KINDS).toHaveLength(4);
  });
});

describe('persistence', () => {
  it('saveCityPresentation writes the record under the injected root, atomically, and it reads back identical', () => {
    const sent: Array<Record<string, unknown>> = [];
    const out = handleDiClientMessage(
      { type: 'saveCityPresentation', state: choice },
      (m) => sent.push(m),
      ctx(),
    );
    expect(out).toBe('handled');
    expect(fs.existsSync(fileOf())).toBe(true);
    expect(fs.existsSync(fileOf() + '.tmp')).toBe(false);
    expect(readCityPresentation()).toEqual(choice);
    expect(sent).toEqual([]); // a save is not answered
    // the bytes on disk are the compact STORED record (the golden above, byte for byte), inside the 256-byte budget
    expect(fs.readFileSync(fileOf(), 'utf-8')).toBe(CHOICE_STORED);
    expect(fs.statSync(fileOf()).size).toBe(CHOICE_STORED.length);
    expect(fs.statSync(fileOf()).size).toBeLessThanOrEqual(CITY_PRESENTATION_V2_MAX_BYTES);
    expect(fs.statSync(fileOf()).size).toBeLessThanOrEqual(BUDGET_BYTES);
  });

  it('a later record replaces the earlier one', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    const later = defaultCityPresentationV2();
    later.lighting = 'clock';
    later.season = { mode: 'auto', season: 'autumn', paused: true, remaining: 999 };
    expect(writeCityPresentation(later)).toBeNull();
    expect(readCityPresentation()).toEqual(later);
  });

  it('the handshake sends the saved record BEFORE the layout, and null when none was saved', () => {
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

  it('the schedule state round-trips exactly: seed, every region step, wet streak and time left, and the held season', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    const back = readCityPresentation() as unknown as typeof choice;
    expect(back.weather.seed).toBe(20_903);
    expect(back.weather.regions.map((r) => [r.kind, r.step, r.wet, r.remaining])).toEqual([
      ['rain', 3, 1, 123_456],
      ['clear', 9, 0, 200_000],
      ['light-snow', 14, 2, 5],
      ['heavy-snow', 999_999, 1, 300_000],
      ['clear', 0, 0, 0],
    ]);
    expect(back.season).toEqual({
      mode: 'manual',
      season: 'winter',
      paused: false,
      remaining: 1_234_567,
    });
  });
});

describe('migration: a saved v1 file is read as v2, and a read writes nothing', () => {
  const plant = (record: unknown) => {
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(fileOf(), JSON.stringify(record));
  };

  it('a v1 record keeps its lighting and traffic, its weather becomes the same manual choice, and the seasons start Automatic from summer', () => {
    plant(v1);
    const read = readCityPresentation() as unknown as ReturnType<typeof defaultCityPresentationV2>;
    expect(read).toEqual({
      ...defaultCityPresentationV2(),
      lighting: 'night',
      traffic: 'low',
      weather: { ...defaultCityPresentationV2().weather, mode: 'rain' },
    });
    expect(read.season).toEqual({
      mode: 'auto',
      season: 'summer',
      paused: false,
      remaining: 1_800_000,
    });
    // the same function the webview uses gives the same answer
    const m = migrateCityPresentation(v1);
    expect(m.ok && m.state).toEqual(read);
  });

  it('every v1 weather becomes the manual Clear, Rain or Light snow, for every lighting and traffic', () => {
    const expected = { off: 'clear', rain: 'rain', snow: 'light-snow' } as const;
    for (const lighting of CITY_LIGHTING_MODES)
      for (const weather of CITY_WEATHER_MODES)
        for (const traffic of CITY_TRAFFIC_MODES) {
          plant({ v: 1, lighting, weather, traffic });
          const read = readCityPresentation() as unknown as ReturnType<
            typeof defaultCityPresentationV2
          >;
          expect([read.lighting, read.traffic, read.weather.mode]).toEqual([
            lighting,
            traffic,
            expected[weather],
          ]);
        }
  });

  it('reading a v1 file WRITES NOTHING: the file is byte-identical afterwards and no other file appears', () => {
    plant(v1);
    const before = fs.readFileSync(fileOf(), 'utf-8');
    readCityPresentation();
    readCityPresentation();
    handshake();
    expect(fs.readFileSync(fileOf(), 'utf-8')).toBe(before);
    expect(fs.readdirSync(root)).toEqual([CITY_PRESENTATION_FILE_NAME]);
  });

  it('the handshake sends the MIGRATED v2 record before the layout; the next save replaces the v1 file with v2', () => {
    plant(v1);
    const sent = handshake();
    const types = sent.map((m) => m.type);
    expect(types.indexOf('cityPresentationLoaded')).toBeLessThan(types.indexOf('layoutLoaded'));
    const state = (
      sent.find((m) => m.type === 'cityPresentationLoaded') as { state: Record<string, unknown> }
    ).state;
    expect(state.v).toBe(2);
    expect(checkCityPresentationV2(state)).toBeNull();
    expect(writeCityPresentation(choice)).toBeNull();
    expect(JSON.parse(fs.readFileSync(fileOf(), 'utf-8')).v).toBe(2);
  });

  it('an invalid v1 or v2 file is ignored WHOLE: the read is null, nothing is rewritten, nothing else is touched', () => {
    const stored = (change: (r: ReturnType<typeof storedObject>) => void): unknown => {
      const r = storedObject();
      change(r);
      return r;
    };
    const planted: unknown[] = [
      { v: 1, lighting: 'night', weather: 'hail', traffic: 'low' },
      { v: 1, lighting: 'night', weather: 'rain', traffic: 'low', extra: 1 },
      stored((r) => (r.s[3] = -1)),
      stored((r) => r.w[2].pop()),
      stored((r) => (r.w[2][0][0] = 9)),
      stored((r) => (r.x = 1)),
      choice, // the NAMED record is not a stored record (and would not fit the budget)
      { v: 3, lighting: 'day', weather: 'off', traffic: 'off' },
    ];
    for (const record of planted) {
      plant(record);
      const before = fs.readFileSync(fileOf(), 'utf-8');
      expect(readCityPresentation(), JSON.stringify(record).slice(0, 50)).toBeNull();
      expect(fs.readFileSync(fileOf(), 'utf-8')).toBe(before);
      expect(fs.readdirSync(root)).toEqual([CITY_PRESENTATION_FILE_NAME]);
    }
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
    expect(fs.existsSync(fileOf())).toBe(false);
    // a v1 save is refused too: the office writes v2 only
    const old = handleDiClientMessage({ type: 'saveCityPresentation', state: v1 }, () => {}, ctx());
    expect(old).toEqual({ refused: 'preference keys differ from the contract' });
    expect(fs.existsSync(fileOf())).toBe(false);
    // the wire carries the NAMED record: a stored-form record is refused as a save too
    const packed = handleDiClientMessage(
      { type: 'saveCityPresentation', state: storedObject() },
      () => {},
      ctx(),
    );
    expect(packed).toEqual({ refused: 'preference keys differ from the contract' });
    expect(fs.existsSync(fileOf())).toBe(false);
    fs.mkdirSync(root, { recursive: true });
    for (const planted of [
      '{"v":2,"lighting":"night"}',
      'x'.repeat(CITY_PRESENTATION_V2_MAX_BYTES + 1),
      JSON.stringify({ ...choice, padding: 'p'.repeat(CITY_PRESENTATION_V2_MAX_BYTES) }),
      'not json',
      '[]',
      JSON.stringify({ ...storedObject(), extra: 1 }),
    ]) {
      fs.writeFileSync(fileOf(), planted);
      expect(readCityPresentation(), planted.slice(0, 30)).toBeNull();
    }
    // a directory where the file belongs is not a file
    fs.rmSync(fileOf());
    fs.mkdirSync(fileOf());
    expect(readCityPresentation()).toBeNull();
  });

  it('THE READ BOUND ALONE decides: a VALID stored record padded with trailing whitespace to exactly 256 bytes loads, one byte more is ignored (JSON and shape both still valid)', () => {
    fs.mkdirSync(root, { recursive: true });
    const padTo = (bytes: number) =>
      CHOICE_STORED + ' '.repeat(bytes - Buffer.byteLength(CHOICE_STORED));
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      // the padded text is the same valid record: JSON.parse ignores trailing whitespace, and the shape check sees no difference
      expect(CITY_PRESENTATION_V2_MAX_BYTES).toBe(BUDGET_BYTES);
      fs.writeFileSync(fileOf(), padTo(CITY_PRESENTATION_V2_MAX_BYTES));
      expect(fs.statSync(fileOf()).size).toBe(256);
      expect(readCityPresentation()).toEqual(choice);
      expect(errors).not.toHaveBeenCalled();
      fs.writeFileSync(fileOf(), padTo(CITY_PRESENTATION_V2_MAX_BYTES + 1));
      const before = fs.readFileSync(fileOf(), 'utf-8');
      expect(fs.statSync(fileOf()).size).toBe(257);
      expect(readCityPresentation()).toBeNull();
      // and the reason logged is the SIZE (so the byte bound, not the JSON or the shape, is what refused it)
      expect(errors).toHaveBeenCalledTimes(1);
      expect(String(errors.mock.calls[0][0])).toContain('257 bytes over 256');
      expect(fs.readFileSync(fileOf(), 'utf-8')).toBe(before);
    } finally {
      errors.mockRestore();
    }
  });

  it('only the preference file is created, inside the root', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    expect(fs.readdirSync(root)).toEqual([CITY_PRESENTATION_FILE_NAME]);
  });

  it('a saved record carries no Mission, agent or position: exactly the five stored keys, only integers, and nested only what the contract names', () => {
    expect(writeCityPresentation(choice)).toBeNull();
    const saved = JSON.parse(fs.readFileSync(fileOf(), 'utf-8'));
    expect(Object.keys(saved).sort()).toEqual(['l', 's', 't', 'v', 'w']);
    expect(saved.s).toHaveLength(4);
    expect(saved.w).toHaveLength(3);
    expect(saved.w[2]).toHaveLength(5);
    for (const region of saved.w[2]) expect(region).toHaveLength(4);
    // nothing in it is text: every value is a small integer code or a bounded integer
    const leaves: unknown[] = [];
    const walk = (v: unknown): void => {
      if (Array.isArray(v)) v.forEach(walk);
      else leaves.push(v);
    };
    walk([saved.v, saved.l, saved.t, saved.s, saved.w]);
    expect(leaves.every((x) => typeof x === 'number' && Number.isInteger(x))).toBe(true);
  });
});

describe('resource', () => {
  it('every admissible record fits the 256-byte budget as STORED, and the range is measured exactly', () => {
    let min = Infinity;
    let max = 0;
    for (const lighting of CITY_LIGHTING_MODES)
      for (const traffic of CITY_TRAFFIC_MODES)
        for (const mode of CITY_SEASON_MODES)
          for (const season of CITY_SEASON_NAMES)
            for (const paused of [true, false])
              for (const wm of CITY_WEATHER_V2_MODES)
                for (const kind of CITY_WEATHER_KINDS)
                  for (const big of [false, true]) {
                    const r = defaultCityPresentationV2();
                    r.lighting = lighting;
                    r.traffic = traffic;
                    r.season = { mode, season, paused, remaining: big ? 1_800_000 : 0 };
                    // in Automatic weather the streak agrees with the kind; a manual mode may carry any streak
                    const wet = wm === 'auto' ? (kind === 'clear' ? 0 : big ? 2 : 1) : big ? 2 : 0;
                    r.weather = {
                      mode: wm,
                      seed: big ? 4_294_967_295 : 0,
                      regions: [0, 1, 2, 3, 4].map(() => ({
                        kind,
                        step: big ? 999_999 : 0,
                        wet,
                        remaining: big ? 300_000 : 0,
                      })),
                    };
                    expect(checkCityPresentationV2(r)).toBeNull();
                    const encoded = encodeStoredCityPresentationV2(r);
                    expect(encoded.ok).toBe(true);
                    const bytes = encoded.ok ? encoded.text.length : Infinity;
                    min = Math.min(min, bytes);
                    max = Math.max(max, bytes);
                  }
    expect(min).toBe(95);
    expect(max).toBe(160);
    expect(max).toBeLessThanOrEqual(CITY_PRESENTATION_V2_MAX_BYTES);
  });

  it('THE WORST CASE through the real persistence path: the longest legal values everywhere are 160 bytes ON DISK, inside the unchanged 256-byte budget', () => {
    const worst = defaultCityPresentationV2();
    worst.lighting = 'clock';
    worst.traffic = 'normal';
    worst.season = { mode: 'manual', season: 'winter', paused: true, remaining: 1_800_000 };
    worst.weather = {
      mode: 'heavy-snow',
      seed: 4_294_967_295,
      regions: [0, 1, 2, 3, 4].map(() => ({
        kind: 'heavy-snow' as const,
        step: 999_999,
        wet: 2,
        remaining: 300_000,
      })),
    };
    // saved over the wire as the office does, then measured as the bytes of the file the server wrote
    expect(
      handleDiClientMessage({ type: 'saveCityPresentation', state: worst }, () => {}, ctx()),
    ).toBe('handled');
    expect(fs.statSync(fileOf()).size).toBe(160);
    expect(fs.statSync(fileOf()).size).toBeLessThanOrEqual(BUDGET_BYTES);
    expect(readCityPresentation()).toEqual(worst);
    // the real function's boundary: accepted at its own size, refused one byte under it
    expect(encodeStoredCityPresentationV2(worst, 160).ok).toBe(true);
    expect(encodeStoredCityPresentationV2(worst, 159)).toEqual({
      ok: false,
      reason: 'preferences over 159 bytes',
    });
  });

  it('the ceiling is exactly the 256-byte budget, and the named (wire) record is not subject to it: only its stored form is', () => {
    expect(CITY_PRESENTATION_V2_MAX_BYTES).toBe(256);
    expect(CITY_PRESENTATION_V2_MAX_BYTES).toBe(BUDGET_BYTES);
    // a hostile string in the named record is refused by the shape check, never reaching the packer
    expect(checkCityPresentationV2({ ...choice, lighting: 'night'.padEnd(2000, 'x') })).toBe(
      'lighting mode unknown',
    );
    expect(JSON.stringify(choice).length).toBeGreaterThan(BUDGET_BYTES);
  });
});
