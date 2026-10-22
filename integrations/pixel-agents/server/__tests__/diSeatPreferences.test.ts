/**
 * DI office: the IDENTITY-KEYED SEAT PREFERENCES (P2-C) — where each real character likes to sit, keyed by canonical identity and never by the
 * process-local character number.
 *   ADMISSION    every identity component is bounded by the EXISTING bound along the real path (catalog contract -> server projection -> identity tuple), pinned
 *                against the cited Python constants; the character rules are the existing ones (empty and NUL refused, nothing else); an entry that breaks them is
 *                refused for THAT ENTRY only; a record over its entry or byte budget loses its OLDEST entries deterministically, never wholesale.
 *   EXACT KEYS   a key is never truncated, normalised or case-folded, stored or matched.
 *   PERSISTENCE  written atomically under the injected root, sent in the handshake, honoured across a simulated server restart.
 *   RESTART      the number-keyed seats of an earlier process are never offered: a number could name a different agent now.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  checkDiSeatPreferences,
  DI_SEAT_PREFERENCE_COVERAGES,
  DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS,
  DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS,
  DI_SEAT_PREFERENCE_TEXT_MAX_CHARS,
  DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES,
  DI_SEAT_PREFERENCES_MAX_BYTES,
  DI_SEAT_PREFERENCES_MAX_ENTRIES,
  DI_SEAT_PREFERENCES_REFUSALS,
  DI_SEAT_PREFERENCES_VERSION,
  type DiSeatPreferenceEntry,
  diSeatPreferenceEntryProblem,
  diSeatPreferencesBytes,
  fitDiSeatPreferences,
  intakeDiSeatPreferences,
  sanitizeDiSeatPreferences,
} from '../../core/src/diSeatPreferences.js';
import { LAYOUT_ID_MAX_LENGTH } from '../../core/src/layoutLimits.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { DI_ID_MAX_LENGTH, DI_SEAT_PREFERENCES_FILE_NAME } from '../src/constants.js';
import { DI_HANDLED_MESSAGE_TYPES, handleDiClientMessage } from '../src/di/diClientMessages.js';
import { readDiSeatPreferences, writeDiSeatPreferences } from '../src/di/diSeatPreferencesPersistence.js';
import { FileStateAdapter } from '../src/fileStateAdapter.js';
import { resetStateRootForTests, setStateRoot } from '../src/stateRoot.js';

const REPO = path.resolve(__dirname, '../../../..');
const python = (rel: string) => fs.readFileSync(path.join(REPO, rel), 'utf-8');
const pyConstant = (source: string, name: string): number => {
  const m = new RegExp(`^${name}\\s*=\\s*(\\d+)\\s*$`, 'm').exec(source);
  if (!m) throw new Error(`${name} not found`);
  return Number(m[1]);
};

/** A minimal agent (the DI handshake reads only palette, hue shift and folder name). */
const agentOf = (id: number) =>
  ({
    id,
    palette: 1,
    hueShift: 0,
    folderName: undefined,
    activeToolIds: new Set(),
    activeToolStatuses: new Map(),
    activeToolNames: new Map(),
    activeSubagentToolIds: new Map(),
    activeSubagentToolNames: new Map(),
    backgroundAgentToolIds: new Set(),
    isWaiting: false,
    permissionSent: false,
    hadToolsInTurn: false,
  }) as unknown as Parameters<AgentStateStore['set']>[1];
function storeWith(adapter: FileStateAdapter, ids: number[]): AgentStateStore {
  const store = new AgentStateStore();
  store.setAdapter(adapter);
  for (const id of ids) store.set(id, agentOf(id));
  return store;
}
const handshakeSeats = (store: AgentStateStore): Record<number, string | undefined> => {
  const sent: Array<Record<string, unknown>> = [];
  handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), { store, cache: EMPTY_CACHE as never, extensionVersion: 'x' });
  const meta = (sent.find((m) => m.type === 'existingAgents') as unknown as { agentMeta: Record<number, { seatId?: string }> }).agentMeta;
  return Object.fromEntries(Object.entries(meta).map(([id, m]) => [Number(id), m.seatId]));
};

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
const entry = (
  over: Partial<{ coverage: string; sourceId: string; missionRef: string; taskId: string | null; memberRef: string; seat: string }> = {},
): DiSeatPreferenceEntry => {
  const e = { coverage: 'direct', sourceId: 'src', missionRef: 'src:mn-1', taskId: 'task-1', memberRef: 'src:mn-1#task-1#lead1', seat: 'expansion-9', ...over };
  return { i: [e.coverage, e.sourceId, e.missionRef, e.taskId, e.memberRef], s: e.seat };
};
const record = (entries: DiSeatPreferenceEntry[]) => ({ v: DI_SEAT_PREFERENCES_VERSION, entries });
/** a distinct valid entry number n with `pad` extra characters in its member ref */
const numbered = (n: number, pad = 0): DiSeatPreferenceEntry =>
  entry({ missionRef: `src:mn-${n}`, memberRef: `src:mn-${n}#t#m${'x'.repeat(pad)}`, seat: `seat-${n}` });

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-seatprefs-')));
  root = path.join(sandbox, 'di-root');
  resetStateRootForTests();
  setStateRoot(root);
});
afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('every bound is pinned against the EXISTING bound it comes from', () => {
  it('the text bound IS the catalog contract\'s MAX_SNAPSHOT_TEXT_CHARS (parity with the Python source), and its rules are require_text\'s: non-empty, no NUL', () => {
    const contract = python('mission_sources/contract.py');
    expect(pyConstant(contract, 'MAX_SNAPSHOT_TEXT_CHARS')).toBe(1024);
    expect(DI_SEAT_PREFERENCE_TEXT_MAX_CHARS).toBe(pyConstant(contract, 'MAX_SNAPSHOT_TEXT_CHARS'));
    // require_text bans exactly: not a str, empty, NUL — and over the bound. The cited rule, as the source states it:
    const requireText = /def require_text[\s\S]*?(?=\ndef |\n# --)/.exec(contract)?.[0] ?? '';
    expect(requireText).toContain('not value or "\\x00" in value');
    expect(requireText).toContain('MAX_SNAPSHOT_TEXT_CHARS');
    // the identity components the contract validates with it
    const v2 = python('mission_sources/contract_v2.py');
    expect(v2).toContain('mission_ref = _text(value["mission_ref"], location + ".mission_ref")');
    expect(v2).toContain('task_id = _text(value["task_id"], location + ".task_id")');
    expect(v2).toContain('logical = _text(value["logical"], location + ".logical")');
    // member_ref is the composition mission_ref#task_id#logical, checked by equality: so its bound is three texts and two separators
    expect(v2).toContain('value["member_ref"] != "%s#%s#%s" % (mission_ref, task_id, logical)');
    expect(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS).toBe(3 * pyConstant(contract, 'MAX_SNAPSHOT_TEXT_CHARS') + 2);
    for (const key of ['agent_ref', 'source_id', 'root_ref']) expect(contract).toContain(`"${key}"`);
  });

  it('the source registry\'s own source id is shorter than the bound, so every registered id fits', () => {
    const registry = python('mission_sources/registry.py');
    const registryMax = pyConstant(registry, 'MAX_SOURCE_ID_CHARS');
    expect(registryMax).toBe(64);
    expect(registryMax).toBeLessThanOrEqual(DI_SEAT_PREFERENCE_TEXT_MAX_CHARS);
  });

  it('the seat id bound IS the layout id bound and the existing saveAgentSeats bound', () => {
    expect(DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS).toBe(LAYOUT_ID_MAX_LENGTH);
    expect(DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS).toBe(DI_ID_MAX_LENGTH);
    expect(DI_SEAT_PREFERENCE_SEAT_ID_MAX_CHARS).toBe(128);
  });

  it('the coverage set is the DI identity map\'s, and the record budget is pinned', () => {
    const identityMap = fs.readFileSync(path.join(__dirname, '../src/di/identityMap.ts'), 'utf-8');
    expect(identityMap).toContain("const COVERAGES: readonly string[] = ['direct', 'child', 'runtime'];");
    expect([...DI_SEAT_PREFERENCE_COVERAGES]).toEqual(['direct', 'child', 'runtime']);
    expect(DI_SEAT_PREFERENCES_MAX_ENTRIES).toBe(512);
    expect(DI_SEAT_PREFERENCES_MAX_BYTES).toBe(131_072);
    expect(DI_SEAT_PREFERENCES_VERSION).toBe(1);
    expect(DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES).toBe(4 * DI_SEAT_PREFERENCES_MAX_BYTES);
    expect(DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES).toBe(524_288);
    expect(DI_SEAT_PREFERENCE_TEXT_MAX_CHARS).toBe(1024);
    expect(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS).toBe(3074);
  });
});

describe('admission: the existing rules, nothing stricter, nothing looser', () => {
  it('a VALID long identity is admissible: a 1000-character task id and a member ref at its full composed bound', () => {
    const longTask = 't'.repeat(1000);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: longTask, memberRef: `src:mn-1#${longTask}#lead1` }))).toBeNull();
    const t = 't'.repeat(1024);
    const full = `${'m'.repeat(1024)}#${t}#${'l'.repeat(1024)}`;
    expect(full.length).toBe(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS);
    expect(diSeatPreferenceEntryProblem(entry({ missionRef: 'm'.repeat(1024), taskId: t, memberRef: full }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ taskId: null }))).toBeNull();
  });

  it('a component over its existing bound, or holding NUL, or empty, is refused — each bound is exact', () => {
    const over = (n: number) => 'x'.repeat(n);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: over(1025) }))).toMatch(/taskId/);
    expect(diSeatPreferenceEntryProblem(entry({ missionRef: over(1025) }))).toMatch(/missionRef/);
    expect(diSeatPreferenceEntryProblem(entry({ sourceId: over(1025) }))).toMatch(/sourceId/);
    expect(diSeatPreferenceEntryProblem(entry({ memberRef: over(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS + 1) }))).toMatch(/memberRef/);
    expect(diSeatPreferenceEntryProblem(entry({ memberRef: over(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS) }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ seat: over(129) }))).toMatch(/seat/);
    expect(diSeatPreferenceEntryProblem(entry({ seat: over(128) }))).toBeNull();
    for (const field of ['sourceId', 'missionRef', 'taskId', 'memberRef'] as const)
      expect(diSeatPreferenceEntryProblem(entry({ [field]: 'a\u0000b' })), `NUL in ${field}`).toMatch(new RegExp(field));
    for (const field of ['sourceId', 'missionRef', 'taskId', 'memberRef'] as const)
      expect(diSeatPreferenceEntryProblem(entry({ [field]: '' })), `empty ${field}`).toMatch(new RegExp(field));
    expect(diSeatPreferenceEntryProblem(entry({ seat: '' }))).toMatch(/seat/);
    expect(diSeatPreferenceEntryProblem(entry({ coverage: 'sideways' }))).toMatch(/coverage/);
  });

  it('the bound counts CODE POINTS like Python\'s len: exactly MAX astral characters are accepted, MAX+1 refused; a mixed BMP + astral string at the boundary; long ASCII', () => {
    const max = DI_SEAT_PREFERENCE_TEXT_MAX_CHARS;
    const astral = '\u{1F600}'; // two UTF-16 units, one code point
    expect(astral.length).toBe(2);
    const edge = astral.repeat(max);
    expect(edge.length).toBe(2 * max); // 2048 UTF-16 units: the old unit count would have refused it
    expect([...edge].length).toBe(max);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: edge, memberRef: `m#${edge}#l` }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ taskId: edge + astral }))).toMatch(/taskId/);
    expect(diSeatPreferenceEntryProblem(entry({ missionRef: edge }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ missionRef: edge + 'a' }))).toMatch(/missionRef/);
    expect(diSeatPreferenceEntryProblem(entry({ sourceId: edge }))).toBeNull();
    // 700 emoji = 700 Python characters = 1400 UTF-16 units: valid
    expect(diSeatPreferenceEntryProblem(entry({ taskId: astral.repeat(700) }))).toBeNull();
    // mixed BMP + astral at the boundary: 1000 ASCII + 24 astral = 1024 code points (1048 units) accepted; 25 astral refused
    const mixedOk = 'a'.repeat(1000) + astral.repeat(24);
    expect(mixedOk.length).toBe(1048);
    expect([...mixedOk].length).toBe(max);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: mixedOk }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ taskId: 'a'.repeat(1000) + astral.repeat(25) }))).toMatch(/taskId/);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: 'é日'.repeat(512) }))).toBeNull(); // BMP, 1024 code points
    expect(diSeatPreferenceEntryProblem(entry({ taskId: 'é日'.repeat(512) + 'x' }))).toMatch(/taskId/);
    // the member ref's composed bound counts code points too
    const memberEdge = astral.repeat(DI_SEAT_PREFERENCE_MEMBER_REF_MAX_CHARS);
    expect(diSeatPreferenceEntryProblem(entry({ memberRef: memberEdge }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ memberRef: memberEdge + astral }))).toMatch(/memberRef/);
    // long ASCII: exactly the bound, and one more
    expect(diSeatPreferenceEntryProblem(entry({ taskId: 'x'.repeat(max) }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ taskId: 'x'.repeat(max + 1) }))).toMatch(/taskId/);
    // NUL is still refused wherever it sits, and a lone surrogate counts as one code point (never silently dropped)
    expect(diSeatPreferenceEntryProblem(entry({ taskId: astral.repeat(10) + '\u0000' }))).toMatch(/taskId/);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: '\ud800'.repeat(max) }))).toBeNull();
    expect(diSeatPreferenceEntryProblem(entry({ taskId: '\ud800'.repeat(max + 1) }))).toMatch(/taskId/);
  });

  it('a hostile huge field is refused without being read through (the bound is checked without materialising code points)', () => {
    const started = performance.now();
    const huge = 'x'.repeat(5_000_000);
    expect(diSeatPreferenceEntryProblem(entry({ taskId: huge }))).toMatch(/taskId/);
    expect(diSeatPreferenceEntryProblem(entry({ memberRef: huge }))).toMatch(/memberRef/);
    expect(performance.now() - started).toBeLessThan(250);
  });

  it('there is NO extra character ban: control characters other than NUL, whitespace and any Unicode are admissible (the contract has no such rule)', () => {
    for (const odd of ['a\u0001b', 'line\nbreak', '\ttab', ' padded ', 'tâche-日本語-😀', 'a\u007fb'])
      expect(diSeatPreferenceEntryProblem(entry({ taskId: odd, memberRef: `m#${odd}#l` })), JSON.stringify(odd)).toBeNull();
  });

  it('an entry with the wrong shape is refused: keys, arity, types', () => {
    for (const bad of [null, 'x', [], { i: ['direct', 'a', 'b', null, 'c'] }, { i: ['direct', 'a', 'b', null], s: 'x' }, { i: ['direct', 'a', 'b', null, 'c'], s: 'x', extra: 1 }, { i: ['direct', 1, 'b', null, 'c'], s: 'x' }, { i: ['direct', 'a', 'b', 3, 'c'], s: 'x' }, { i: ['direct', 'a', 'b', null, 'c'], s: 4 }])
      expect(diSeatPreferenceEntryProblem(bad), JSON.stringify(bad)).not.toBeNull();
  });
});

describe('INTERNAL fitting (the reader of a bounded file, the webview map): a bad entry is refused for THAT ENTRY only, and the refusal is reported', () => {
  it('the rest of the record stands; the later of two identical identities stands', () => {
    const good1 = numbered(1);
    const good2 = numbered(2);
    const bad = entry({ taskId: 'x'.repeat(2000) });
    const nul = entry({ missionRef: 'a\u0000b' });
    const again = { ...numbered(1), s: 'moved' };
    const clean = sanitizeDiSeatPreferences(record([good1, bad, good2, nul, again]))!;
    expect(clean.state.entries).toEqual([good2, again]);
    expect(clean.refused).toHaveLength(3);
    // reported in entry order: entry 0 is superseded by the later identical identity, entry 1 breaks the taskId bound, entry 3 holds NUL
    expect(clean.refused[0]).toMatch(/entry 0: .*listed again later/);
    expect(clean.refused[1]).toMatch(/entry 1: .*taskId/);
    expect(clean.refused[2]).toMatch(/entry 3: .*missionRef/);
    expect(clean.evicted).toBe(0);
  });

  it('only a value that is not a record at all is refused whole', () => {
    for (const bad of [null, 'x', [], { v: 2, entries: [] }, { v: 1 }, { v: 1, entries: 'x' }, { v: 1, entries: [], extra: 1 }])
      expect(sanitizeDiSeatPreferences(bad), JSON.stringify(bad)).toBeNull();
    expect(writeDiSeatPreferences({ v: 1, entries: 'x' })).toMatch(/not a record/);
    expect(fs.existsSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME))).toBe(false);
  });

  it('through the handler: a RECEIVED save with one bad entry is refused WHOLE (closed reason, nothing written); a non-record is refused too', () => {
    const ctx = { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' };
    expect(handleDiClientMessage({ type: 'saveDiSeatPreferences', state: record([numbered(1), numbered(2)]) }, () => undefined, ctx)).toBe('handled');
    const file = path.join(root, DI_SEAT_PREFERENCES_FILE_NAME);
    const before = fs.readFileSync(file);
    const secret = 'SECRET-MARKER-THAT-MUST-NEVER-BE-ECHOED';
    const out = handleDiClientMessage({ type: 'saveDiSeatPreferences', state: record([numbered(7), entry({ taskId: secret.repeat(100) }), numbered(8)]) }, () => undefined, ctx) as { refused: string };
    expect(typeof out).toBe('object');
    expect(out.refused).toMatch(/taskId/);
    expect(out.refused).not.toContain(secret);
    expect(fs.readFileSync(file).equals(before)).toBe(true); // byte-identical: nothing was written
    const refused = handleDiClientMessage({ type: 'saveDiSeatPreferences', state: 'x' }, () => undefined, ctx);
    expect(refused).toEqual({ refused: DI_SEAT_PREFERENCES_REFUSALS.notARecord });
    expect(fs.readFileSync(file).equals(before)).toBe(true);
    expect(DI_HANDLED_MESSAGE_TYPES).toContain('saveDiSeatPreferences');
  });
});

describe('the record budget: the OLDEST entries are evicted, deterministically; an over-budget save never fails wholesale', () => {
  it('by entry count: 600 entries keep the newest 512', () => {
    const entries = Array.from({ length: 600 }, (_, n) => numbered(n));
    const fitted = fitDiSeatPreferences(entries);
    expect(fitted.entries).toHaveLength(DI_SEAT_PREFERENCES_MAX_ENTRIES);
    expect(fitted.evicted).toBe(88);
    expect(fitted.entries[0]).toEqual(numbered(88));
    expect(fitted.entries.at(-1)).toEqual(numbered(599));
  });

  it('by bytes: long VALID identities exceed the byte budget well before the entry budget, and the newest entries are kept', () => {
    const entries = Array.from({ length: 60 }, (_, n) => numbered(n, 2900)); // each entry ~3 KB
    expect(diSeatPreferencesBytes(record(entries))).toBeGreaterThan(DI_SEAT_PREFERENCES_MAX_BYTES);
    const clean = sanitizeDiSeatPreferences(record(entries))!;
    expect(clean.evicted).toBeGreaterThan(0);
    expect(clean.state.entries.at(-1)).toEqual(numbered(59, 2900));
    expect(clean.state.entries[0]).toEqual(numbered(60 - clean.state.entries.length, 2900));
    expect(diSeatPreferencesBytes(clean.state)).toBeLessThanOrEqual(DI_SEAT_PREFERENCES_MAX_BYTES);
    // one fewer eviction would not have fit: the eviction is minimal
    const oneMore = record([numbered(60 - clean.state.entries.length - 1, 2900), ...clean.state.entries]);
    expect(diSeatPreferencesBytes(oneMore)).toBeGreaterThan(DI_SEAT_PREFERENCES_MAX_BYTES);
    // the result passes the strict whole-record check, and the same input always gives the same result
    expect(checkDiSeatPreferences(clean.state)).toBeNull();
    expect(sanitizeDiSeatPreferences(record(entries))).toEqual(clean);
  });

  it('a save over the byte budget is WRITTEN (trimmed oldest-first) and read back as the same newest entries', () => {
    const entries = Array.from({ length: 60 }, (_, n) => numbered(n, 2900));
    expect(writeDiSeatPreferences(record(entries))).toBeNull();
    const back = readDiSeatPreferences() as unknown as { entries: DiSeatPreferenceEntry[] };
    expect(back.entries.at(-1)).toEqual(numbered(59, 2900));
    expect(fs.statSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME)).size).toBeLessThanOrEqual(DI_SEAT_PREFERENCES_MAX_BYTES);
  });

  it('the byte count is UTF-8: multi-byte identities count what they weigh', () => {
    const value = { s: 'aé日😀' };
    expect(diSeatPreferencesBytes(value)).toBe(new TextEncoder().encode(JSON.stringify(value)).length);
    expect(diSeatPreferencesBytes(value)).toBeGreaterThan(JSON.stringify(value).length);
  });
});

describe('a key is exactly the identity: never truncated, normalised or case-folded, stored or matched', () => {
  it('a 1000-character identity round-trips whole; look-alike keys stay distinct', () => {
    const long = 't'.repeat(1000);
    const nfc = 'café';
    const nfd = 'café';
    const entries = [
      entry({ taskId: long, memberRef: `src:mn-1#${long}#lead1`, seat: 'a' }),
      entry({ taskId: 'Task', memberRef: 'm1', seat: 'b' }),
      entry({ taskId: 'task', memberRef: 'm1', seat: 'c' }), // case differs: another identity
      entry({ taskId: nfc, memberRef: 'm2', seat: 'd' }),
      entry({ taskId: nfd, memberRef: 'm2', seat: 'e' }), // canonically equivalent, a different identity: never normalised
      entry({ taskId: 'task ', memberRef: 'm1', seat: 'f' }), // trailing space kept
    ];
    expect(writeDiSeatPreferences(record(entries))).toBeNull();
    const back = readDiSeatPreferences() as unknown as { entries: DiSeatPreferenceEntry[] };
    expect(back.entries).toEqual(entries);
    expect(back.entries[0].i[3]).toHaveLength(1000);
    expect(new Set(back.entries.map((e) => JSON.stringify(e.i))).size).toBe(entries.length);
  });
});

describe('persistence and restart', () => {
  it('writes atomically under the injected root and sends the record in the handshake after existingAgents and before the layout', () => {
    const state = record([numbered(1), numbered(2)]);
    expect(handleDiClientMessage({ type: 'saveDiSeatPreferences', state }, () => undefined, { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' })).toBe('handled');
    expect(fs.existsSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME))).toBe(true);
    expect(fs.existsSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME + '.tmp'))).toBe(false);
    const sent: Array<Record<string, unknown>> = [];
    handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    const types = sent.map((m) => m.type);
    expect(types.indexOf('diSeatPreferencesLoaded')).toBe(types.indexOf('existingAgents') + 1);
    expect(types.indexOf('diSeatPreferencesLoaded')).toBeLessThan(types.indexOf('layoutLoaded'));
    expect(sent.find((m) => m.type === 'diSeatPreferencesLoaded')).toEqual({ type: 'diSeatPreferencesLoaded', state });
  });

  it('with nothing saved the handshake sends null; a hand-planted file that is not a record, or is over the file ceiling, is ignored', () => {
    const sent: Array<Record<string, unknown>> = [];
    handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    expect(sent.find((m) => m.type === 'diSeatPreferencesLoaded')).toEqual({ type: 'diSeatPreferencesLoaded', state: null });
    fs.mkdirSync(root, { recursive: true });
    fs.writeFileSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME), '{"v":1,"entries":"nope"}');
    expect(readDiSeatPreferences()).toBeNull();
    fs.writeFileSync(path.join(root, DI_SEAT_PREFERENCES_FILE_NAME), ' '.repeat(DI_SEAT_PREFERENCES_MAX_BYTES + 1));
    expect(readDiSeatPreferences()).toBeNull();
  });

  it('a VALID long identity persists and is honoured across a simulated server restart', () => {
    const longTask = 'T'.repeat(1000);
    const long = entry({ taskId: longTask, memberRef: `src:mn-1#${longTask}#lead1`, seat: 'expansion-77' });
    // process 1: the client saves its preferences
    handleDiClientMessage({ type: 'saveDiSeatPreferences', state: record([numbered(5), long]) }, () => undefined, { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    // process 2 (a restart: a brand-new store, nothing in memory, the same state root): the handshake carries the whole 1000-character identity
    const sent: Array<Record<string, unknown>> = [];
    handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), { store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' });
    const loaded = sent.find((m) => m.type === 'diSeatPreferencesLoaded') as unknown as { state: { entries: DiSeatPreferenceEntry[] } };
    const got = loaded.state.entries.find((e) => e.s === 'expansion-77')!;
    expect(got.i[3]).toBe(longTask);
    expect(got.i[4]).toBe(`src:mn-1#${longTask}#lead1`);
    expect(loaded.state.entries).toEqual([numbered(5), long]);
  });

  it('RESTART: the number-keyed seats of an earlier process are never offered; the seats saved by THIS process are', () => {
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    // an earlier process left number-keyed seats in the adapter state file (numbers THAT process assigned)
    adapter.saveSeats({ '1': { palette: 2, seatId: 'old-seat-1' }, '2': { palette: 3, seatId: 'old-seat-2' } });
    expect(adapter.loadSeats()['1'].seatId).toBe('old-seat-1');
    // a new process: agents 1 and 2 now name DIFFERENT identities, so the old seats must not attach to them
    expect(handshakeSeats(storeWith(adapter, [1, 2]))).toEqual({ 1: undefined, 2: undefined });
    // THIS process: the client saves seats for the numbers it is using now; the next handshake of the SAME process offers exactly those
    const store = storeWith(adapter, [1, 2]);
    expect(
      handleDiClientMessage({ type: 'saveAgentSeats', seats: { '1': { seatId: 'now-seat-1' } } }, () => undefined, { store, cache: EMPTY_CACHE as never, extensionVersion: 'x' }),
    ).toBe('handled');
    expect(handshakeSeats(store)).toEqual({ 1: 'now-seat-1', 2: undefined });
    // another restart (a different store): none again, although the file still holds this process's numbers too
    expect(handshakeSeats(storeWith(adapter, [1, 2]))).toEqual({ 1: undefined, 2: undefined });
  });
});

describe('external intake: cheap, bounded, whole-or-nothing, closed reasons', () => {
  const ctx = () => ({ store: new AgentStateStore(), cache: EMPTY_CACHE as never, extensionVersion: 'x' });
  const file = () => path.join(root, DI_SEAT_PREFERENCES_FILE_NAME);
  const seed = () => {
    expect(handleDiClientMessage({ type: 'saveDiSeatPreferences', state: record([numbered(1), numbered(2)]) }, () => undefined, ctx())).toBe('handled');
    return fs.readFileSync(file());
  };
  const refusedWith = (state: unknown) => handleDiClientMessage({ type: 'saveDiSeatPreferences', state }, () => undefined, ctx()) as { refused: string };

  it('MAX_ENTRIES + 1 entries are refused before any entry is read; the saved file is byte-identical', () => {
    const before = seed();
    const tooMany = Array.from({ length: DI_SEAT_PREFERENCES_MAX_ENTRIES + 1 }, (_, n) => numbered(n));
    expect(refusedWith(record(tooMany)).refused).toBe(DI_SEAT_PREFERENCES_REFUSALS.tooMany);
    expect(fs.readFileSync(file()).equals(before)).toBe(true);
    // exactly MAX_ENTRIES is fine
    expect(intakeDiSeatPreferences(record(tooMany.slice(0, DI_SEAT_PREFERENCES_MAX_ENTRIES))).ok).toBe(true);
  });

  it('a frame-sized array is refused by its LENGTH alone, without being walked', () => {
    const started = performance.now();
    const entries = new Array<DiSeatPreferenceEntry>(3_000_000).fill(numbered(1));
    const r = intakeDiSeatPreferences({ v: 1, entries });
    expect(r).toEqual({ ok: false, reason: DI_SEAT_PREFERENCES_REFUSALS.tooMany });
    expect(performance.now() - started).toBeLessThan(500); // dominated by building the test array, not by the intake
    const t0 = performance.now();
    intakeDiSeatPreferences({ v: 1, entries });
    expect(performance.now() - t0).toBeLessThan(25);
  });

  it('an over-bound field, a NUL, and a huge field are each refused whole: byte-identical file, closed non-echoing reason', () => {
    const before = seed();
    const secret = 'LEAK-CANARY';
    for (const bad of [
      record([numbered(5), entry({ taskId: 'x'.repeat(1025) })]),
      record([numbered(5), entry({ memberRef: `a\u0000${secret}` })]),
      record([numbered(5), entry({ missionRef: secret.repeat(400_000) })]), // ~4.4 MB in one field
      record([numbered(5), entry({ seat: 's'.repeat(129) })]),
      record([numbered(5), entry({ coverage: secret })]),
    ]) {
      const out = refusedWith(bad);
      expect(typeof out.refused).toBe('string');
      expect(out.refused).not.toContain(secret);
      expect(fs.readFileSync(file()).equals(before)).toBe(true);
    }
  });

  it('a repeated identity is refused whole (a correct client never sends one)', () => {
    const before = seed();
    expect(refusedWith(record([numbered(3), numbered(3)])).refused).toBe(DI_SEAT_PREFERENCES_REFUSALS.duplicate);
    expect(fs.readFileSync(file()).equals(before)).toBe(true);
  });

  it('a save whose estimated size passes the intake bound is refused unread; one a little over the byte BUDGET is accepted and trimmed oldest-first', () => {
    const before = seed();
    // 200 entries of ~3 KB: far over the intake bound
    const huge = Array.from({ length: 200 }, (_, n) => numbered(n, 2900));
    expect(refusedWith(record(huge)).refused).toBe(DI_SEAT_PREFERENCES_REFUSALS.tooLarge);
    expect(fs.readFileSync(file()).equals(before)).toBe(true);
    // 60 entries of ~3 KB (about 180 KB): in bound per entry and in count, over the byte budget: the newest are kept
    const slightlyOver = Array.from({ length: 60 }, (_, n) => numbered(n, 2900));
    expect(diSeatPreferencesBytes(record(slightlyOver))).toBeGreaterThan(DI_SEAT_PREFERENCES_MAX_BYTES);
    expect(diSeatPreferencesBytes(record(slightlyOver))).toBeLessThan(DI_SEAT_PREFERENCES_INTAKE_MAX_BYTES);
    expect(handleDiClientMessage({ type: 'saveDiSeatPreferences', state: record(slightlyOver) }, () => undefined, ctx())).toBe('handled');
    const back = readDiSeatPreferences() as unknown as { entries: DiSeatPreferenceEntry[] };
    expect(back.entries.at(-1)).toEqual(numbered(59, 2900));
    expect(back.entries[0]).not.toEqual(numbered(0, 2900));
    expect(fs.statSync(file()).size).toBeLessThanOrEqual(DI_SEAT_PREFERENCES_MAX_BYTES);
  });

  it('an ordinary save exactly at the byte limit is written whole; the read of a too-large existing file is refused by the existing read bound', () => {
    // fill the record to within a few bytes of the budget with valid entries
    const entries: DiSeatPreferenceEntry[] = [];
    let n = 0;
    while (diSeatPreferencesBytes(record([...entries, numbered(n, 1000)])) <= DI_SEAT_PREFERENCES_MAX_BYTES) entries.push(numbered(n++, 1000));
    expect(diSeatPreferencesBytes(record(entries))).toBeLessThanOrEqual(DI_SEAT_PREFERENCES_MAX_BYTES);
    expect(writeDiSeatPreferences(record(entries))).toBeNull();
    expect((readDiSeatPreferences() as unknown as { entries: unknown[] }).entries).toHaveLength(entries.length);
    fs.writeFileSync(file(), ' '.repeat(DI_SEAT_PREFERENCES_MAX_BYTES + 1));
    expect(readDiSeatPreferences()).toBeNull();
  });

  it('the internal paths stay internal: sanitising a bounded record still refuses per entry and evicts oldest-first (the reader and the webview map)', () => {
    const clean = sanitizeDiSeatPreferences(record([numbered(1), entry({ taskId: '' }), numbered(2)]))!;
    expect(clean.state.entries).toEqual([numbered(1), numbered(2)]);
    expect(clean.refused).toHaveLength(1);
  });
});
