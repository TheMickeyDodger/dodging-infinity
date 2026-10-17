/**
 * DI office: the decorative AMBIENT SNAPSHOT (M3 S4) — the four test kinds for a new persisted file:
 * ADMISSION (only the contract shape within its bounds is written), PERSISTENCE (written atomically
 * under the injected root and read back identical; sent in the handshake before the layout),
 * SECURITY (malformed, oversized, duplicated or hand-planted files are refused or ignored; nothing
 * lands outside the root), RESOURCE (the byte ceiling holds for the shipped population).
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  AMBIENT_SAVE_FINGERPRINT_MAX,
  AMBIENT_SAVE_ID_MAX,
  AMBIENT_SAVE_MAX_ABOARD,
  AMBIENT_SAVE_MAX_BYTES,
  AMBIENT_SAVE_MAX_RESIDENTS,
  AMBIENT_SAVE_MAX_TRAINS,
  AMBIENT_SAVE_TRAIN_ID_MAX,
  ambientSaveBytes,
  checkAmbientSave,
} from '../../core/src/ambientSave.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { AMBIENT_STATE_FILE_NAME } from '../src/constants.js';
import { readAmbientState, writeAmbientState } from '../src/di/ambientStatePersistence.js';
import { handleDiClientMessage } from '../src/di/diClientMessages.js';
import { resetStateRootForTests, setStateRoot } from '../src/stateRoot.js';

let sandbox: string;
let root: string;
const EMPTY_CACHE = { characters: null, pets: null, floorTiles: null, wallTiles: null, carpetTiles: null, furniture: null, defaultLayout: null };

// v2: [id, routineIndex, col, row, state, timer]; trains [id, dir, x, next, row, state, timer, speed, onboard]
// resident k=3 is aboard (state 3) and listed on train-1, as the linkage requires
const snapshot = (n = 4) => ({
  v: 2,
  world: '352x312|r2|tdeadbeef|f01234567|a01234567|n4',
  savedAt: 1_792_000_000_000,
  residents: Array.from({ length: n }, (_, k) => [`amb-test-${k}`, k % 4, 10 + k, 20, k === 3 ? 3 : k % 3, k === 3 ? 0 : 1.5]),
  trains: [
    ['train-1', 1, 40.5, 1, 306, 0, 0, 4, n > 3 ? ['amb-test-3'] : []],
    ['train-2', -1, 201, 2, 308, 1, 3.25, 0, []],
  ],
});

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-ambient-')));
  root = path.join(sandbox, 'di-root');
  resetStateRootForTests();
  setStateRoot(root);
});
afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('admission', () => {
  it('the contract admits a well-formed snapshot and refuses every malformed shape', () => {
    expect(checkAmbientSave(snapshot())).toBeNull();
    const bad: Array<[string, unknown]> = [
      ['not an object', 'x'],
      ['extra key', { ...snapshot(), extra: 1 }],
      ['the previous version', { ...snapshot(), v: 1 }],
      ['duplicate id', { ...snapshot(), residents: [['a', 0, 1, 1, 0, 0], ['a', 0, 2, 2, 0, 0]], trains: [] }],
      ['tile out of bounds', { ...snapshot(), residents: [['a', 0, 999, 1, 0, 0]], trains: [] }],
      ['bad state code', { ...snapshot(), residents: [['a', 0, 1, 1, 7, 0]], trains: [] }],
      ['NaN timer', { ...snapshot(), residents: [['a', 0, 1, 1, 0, Number.NaN]], trains: [] }],
      ['v1-shaped resident', { ...snapshot(), residents: [['a', 0, 1, 1, 0]], trains: [] }],
      ['non-ASCII id', { ...snapshot(), residents: [['á', 0, 1, 1, 0, 0]], trains: [] }],
      ['too many residents', snapshot(201)],
      ['bad train direction', { ...snapshot(), trains: [['t', 0, 1, 0, 1, 0, 0, 0, []]] }],
      ['bad train state', { ...snapshot(), trains: [['t', 1, 1, 0, 1, 9, 0, 0, []]] }],
      ['too many trains', { ...snapshot(), trains: Array.from({ length: 5 }, (_, k) => [`t${k}`, 1, 1, 0, 1, 0, 0, 0, []]) }],
      ['a resident aboard no train', { ...snapshot(), trains: [['train-1', 1, 40.5, 1, 306, 0, 0, 4, []]] }],
      ['a passenger that is not aboard', { ...snapshot(), trains: [['train-1', 1, 40.5, 1, 306, 0, 0, 4, ['amb-test-3', 'amb-test-0']]] }],
      ['a passenger on two trains', { ...snapshot(), trains: [['train-1', 1, 40.5, 1, 306, 0, 0, 4, ['amb-test-3']], ['train-2', -1, 201, 2, 308, 1, 3.25, 0, ['amb-test-3']]] }],
      ['an unknown passenger', { ...snapshot(), trains: [['train-1', 1, 40.5, 1, 306, 0, 0, 4, ['amb-test-3', 'nobody']]] }],
    ];
    for (const [name, value] of bad) expect(checkAmbientSave(value), name).not.toBeNull();
  });
});

describe('persistence', () => {
  it('saveAmbientState writes the snapshot under the injected root, and it reads back identical', () => {
    const sent: Array<Record<string, unknown>> = [];
    const store = new AgentStateStore();
    const out = handleDiClientMessage({ type: 'saveAmbientState', state: snapshot() }, (m) => sent.push(m), { store, cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    expect(out).toBe('handled');
    expect(fs.existsSync(path.join(root, AMBIENT_STATE_FILE_NAME))).toBe(true);
    expect(readAmbientState()).toEqual(snapshot());
    expect(sent).toEqual([]);
  });

  it('the handshake sends the saved snapshot BEFORE the layout (and null when none exists)', () => {
    const store = new AgentStateStore();
    const run = () => {
      const sent: Array<Record<string, unknown>> = [];
      handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), { store, cache: EMPTY_CACHE as never, extensionVersion: 'x' });
      return sent;
    };
    let sent = run();
    expect(sent.find((m) => m.type === 'ambientStateLoaded')).toEqual({ type: 'ambientStateLoaded', state: null });
    expect(writeAmbientState(snapshot())).toBeNull();
    sent = run();
    const types = sent.map((m) => m.type);
    expect(types.indexOf('ambientStateLoaded')).toBeLessThan(types.indexOf('layoutLoaded'));
    expect(sent.find((m) => m.type === 'ambientStateLoaded')).toEqual({ type: 'ambientStateLoaded', state: snapshot() });
  });
});

describe('security', () => {
  it('a refused save writes nothing; a planted malformed or oversized file is ignored on read', () => {
    const store = new AgentStateStore();
    const out = handleDiClientMessage({ type: 'saveAmbientState', state: { ...snapshot(), v: 99 } }, () => {}, { store, cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    // a snapshot of the previous version (v1, no journeys) is refused as well, never half-read
    expect(out).toEqual({ refused: 'snapshot version unsupported' });
    expect(fs.existsSync(path.join(root, AMBIENT_STATE_FILE_NAME))).toBe(false);
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, AMBIENT_STATE_FILE_NAME), '{"v":1,"world":"x"}');
    expect(readAmbientState()).toBeNull();
    fs.writeFileSync(path.join(root, AMBIENT_STATE_FILE_NAME), 'x'.repeat(AMBIENT_SAVE_MAX_BYTES + 1));
    expect(readAmbientState()).toBeNull();
    fs.writeFileSync(path.join(root, AMBIENT_STATE_FILE_NAME), 'not json');
    expect(readAmbientState()).toBeNull();
  });

  it('only the snapshot file is created, inside the root', () => {
    expect(writeAmbientState(snapshot())).toBeNull();
    expect(fs.readdirSync(root)).toEqual([AMBIENT_STATE_FILE_NAME]);
  });
});

describe('resource', () => {
  // The v2 shape (journeys: per-rider linkage, timers, train state) at EVERY contract bound at once:
  // 200 residents with 128-character ids, 32 aboard (also listed on trains), 4 trains with
  // 32-character ids, a 256-character fingerprint and the widest integers, with the id and
  // fingerprint text filled by one character. Measured:
  // - UNESCAPED fill (any printable ASCII but `"` and `\`), serializer-shaped numbers (3 decimals):
  //   35,678 B; with the LONGEST JSON a finite in-bounds number can have (24 characters,
  //   0.0000012345678901234567): 38,488 B. That is the longest admissible UNESCAPED shape.
  // - ESCAPED fill (`"` or `\`, two JSON bytes per character) at the same lengths: OVER the
  //   65,536 B ceiling. Length and count bounds do not prevent that; the byte ceiling REFUSES it,
  //   at the exact boundary below.
  const id = (k: number, n: number, fill: string) => (`${k}-` + fill.repeat(n)).slice(0, n);
  const worst = (num: () => number, fill = 'x') => {
    const residents = Array.from({ length: AMBIENT_SAVE_MAX_RESIDENTS }, (_, k) => [id(k, AMBIENT_SAVE_ID_MAX, fill), 63, 383, 383, k < AMBIENT_SAVE_MAX_ABOARD ? 3 : 4, k < AMBIENT_SAVE_MAX_ABOARD ? 0 : num()]);
    const aboard = residents.slice(0, AMBIENT_SAVE_MAX_ABOARD).map((r) => r[0] as string);
    const trains = Array.from({ length: AMBIENT_SAVE_MAX_TRAINS }, (_, t) => [id(t, AMBIENT_SAVE_TRAIN_ID_MAX, fill), -1, num() % 383, 63, 383, 3, num(), num() % 63, aboard.slice(t * 8, t * 8 + 8)]);
    return { v: 2, world: fill.repeat(AMBIENT_SAVE_FINGERPRINT_MAX), savedAt: num() > 1 ? Number.MAX_SAFE_INTEGER : 1234567890123.4568, residents, trains };
  };
  const LONGEST_NUMBER = 0.0000012345678901234567;

  it('the longest admissible UNESCAPED v2 shape at every contract bound fits the ceiling (measured)', () => {
    const serializerShaped = worst(() => 3599.999);
    const longest = worst(() => LONGEST_NUMBER);
    expect(JSON.stringify(LONGEST_NUMBER)).toHaveLength(24);
    expect(checkAmbientSave(serializerShaped)).toBeNull();
    expect(checkAmbientSave(longest)).toBeNull();
    expect(ambientSaveBytes(serializerShaped)).toBe(35_678);
    expect(ambientSaveBytes(longest)).toBe(38_488);
  });

  it('ESCAPED strings (quote or backslash fill, same lengths) exceed the ceiling, and the byte guard refuses them', () => {
    for (const fill of ['"', '\\']) {
      const s = worst(() => LONGEST_NUMBER, fill);
      expect(ambientSaveBytes(s), fill).toBe(JSON.stringify(s).length);
      expect(ambientSaveBytes(s), fill).toBeGreaterThan(AMBIENT_SAVE_MAX_BYTES);
      expect(checkAmbientSave(s), fill).toBe(`snapshot over ${AMBIENT_SAVE_MAX_BYTES} bytes`);
      expect(writeAmbientState(s), fill).not.toBeNull();
      expect(fs.existsSync(path.join(root, AMBIENT_STATE_FILE_NAME)), fill).toBe(false);
    }
  });

  it('the exact boundary with escaped strings: 65,536 B is admitted, 65,537 B is refused', () => {
    const s = worst(() => LONGEST_NUMBER, '"');
    // un-escape characters of ids that appear ONCE (residents not aboard): each removes one byte
    const excess = ambientSaveBytes(s) - AMBIENT_SAVE_MAX_BYTES;
    expect(excess).toBeGreaterThan(0);
    let left = excess;
    let touched = -1;
    for (let k = AMBIENT_SAVE_MAX_ABOARD; k < s.residents.length && left > 0; k++) {
      const old = s.residents[k][0] as string;
      const quotes = [...old].filter((c) => c === '"').length;
      const n = Math.min(quotes, left);
      s.residents[k][0] = old.replace(new RegExp('"'.repeat(n)), 'y'.repeat(n));
      left -= n;
      touched = k;
    }
    expect(left).toBe(0);
    expect(ambientSaveBytes(s)).toBe(AMBIENT_SAVE_MAX_BYTES);
    expect(checkAmbientSave(s)).toBeNull();
    // one character back to a quote: one byte over
    const row = s.residents[touched];
    expect((row[0] as string).includes('y')).toBe(true);
    row[0] = (row[0] as string).replace('y', '"');
    expect(ambientSaveBytes(s)).toBe(AMBIENT_SAVE_MAX_BYTES + 1);
    expect(checkAmbientSave(s)).toBe(`snapshot over ${AMBIENT_SAVE_MAX_BYTES} bytes`);
  });
});
