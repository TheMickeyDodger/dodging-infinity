/**
 * A4 — inert BY CONSTRUCTION, tested at the HANDLER (not by a hidden button):
 * 1. every protocol ClientMessage type outside the DI allowlist does nothing — no
 *    reply, no adapter write, no file write;
 * 2. the DI handshake carries no hooks status, no consent ask, no provider tool list;
 * 3. the DI module graph imports no hook provider, runtime, scanner or consent module.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let sandbox: string;
let fakeHome: string;

vi.mock('os', async () => {
  const actual = await vi.importActual<typeof import('os')>('os');
  return { ...actual, homedir: () => fakeHome };
});

const { AgentStateStore } = await import('../src/agentStateStore.js');
const { resetStateRootForTests, setStateRoot } = await import('../src/stateRoot.js');
const { FileStateAdapter } = await import('../src/fileStateAdapter.js');
const { DI_HANDLED_MESSAGE_TYPES, DI_INERT_MESSAGE_TYPES, handleDiClientMessage, initDiDefaults } =
  await import('../src/di/diClientMessages.js');
const { validateDiLayout } = await import('../src/di/diInputLimits.js');
const { DI_WS_MAX_PAYLOAD_BYTES } = await import('../src/constants.js');

const SRC = path.join(__dirname, '../src');

const EMPTY_CACHE = {
  characters: null,
  pets: null,
  floorTiles: null,
  wallTiles: null,
  carpetTiles: null,
  furniture: null,
  defaultLayout: null,
};

/** Representative payloads for the inert types (as a real client would send). */
const INERT_PAYLOADS: Record<string, Record<string, unknown>> = {
  launchAgent: { type: 'launchAgent', folderPath: '/tmp', bypassPermissions: true },
  focusAgent: { type: 'focusAgent', id: 1 },
  closeAgent: { type: 'closeAgent', id: 1 },
  setHooksEnabled: { type: 'setHooksEnabled', providerId: 'claude', enabled: true },
  hooksConsentResponse: { type: 'hooksConsentResponse', providerId: 'claude', choice: 'install' },
  setHooksInfoShown: { type: 'setHooksInfoShown' },
  setWatchAllSessions: { type: 'setWatchAllSessions', enabled: true },
  openSessionsFolder: { type: 'openSessionsFolder' },
  addExternalAssetDirectory: { type: 'addExternalAssetDirectory', path: '/etc' },
  removeExternalAssetDirectory: { type: 'removeExternalAssetDirectory', path: '/etc' },
  saveAreaMappings: { type: 'saveAreaMappings', mappings: { a: ['b'] } },
  exportLayout: { type: 'exportLayout' },
  importLayout: { type: 'importLayout' },
  requestDiagnostics: { type: 'requestDiagnostics' },
};

function treeOf(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      out.push(path.relative(root, full));
      if (e.isDirectory()) walk(full);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return out.sort();
}

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-handler-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);
  resetStateRootForTests();
  setStateRoot(path.join(sandbox, 'di-root'));
});

afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('DI handler: inert types do nothing at all', () => {
  it('the inert list covers every protocol ClientMessage the DI office does not act on', () => {
    const messagesTs = fs.readFileSync(path.join(__dirname, '../../core/src/messages.ts'), 'utf-8');
    const union = /export type ClientMessage =([^;]*);/.exec(messagesTs)?.[1] ?? '';
    const variants = union
      .split('|')
      .map((v) => v.trim())
      .filter(Boolean);
    expect(variants).toHaveLength(22);
    const declared = new Set<string>([...DI_HANDLED_MESSAGE_TYPES, ...DI_INERT_MESSAGE_TYPES]);
    expect(declared.size).toBe(22);
    for (const type of Object.keys(INERT_PAYLOADS)) expect(DI_INERT_MESSAGE_TYPES).toContain(type);
  });

  for (const [type, payload] of Object.entries(INERT_PAYLOADS)) {
    it(`${type}: no reply, no adapter call, nothing written anywhere`, () => {
      const store = new AgentStateStore();
      const adapter = new FileStateAdapter({ namespace: 'standalone' });
      const spies = [
        vi.spyOn(adapter, 'setSetting'),
        vi.spyOn(adapter, 'saveSeats'),
        vi.spyOn(adapter, 'saveAgents'),
      ];
      store.setAdapter(adapter);
      const send = vi.fn();
      const disposition = handleDiClientMessage(payload, send, {
        store,
        cache: EMPTY_CACHE,
        extensionVersion: 'test',
      });
      expect(disposition).toBe('inert');
      expect(send).not.toHaveBeenCalled();
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
      expect(treeOf(sandbox)).toEqual(['home']); // no DI root file, nothing in the fake HOME
    });
  }

  it('an unknown type is dropped the same way', () => {
    const send = vi.fn();
    const store = new AgentStateStore();
    expect(
      handleDiClientMessage({ type: 'spawnEverything' }, send, {
        store,
        cache: EMPTY_CACHE,
        extensionVersion: '',
      }),
    ).toBe('unknown');
    expect(send).not.toHaveBeenCalled();
  });
});

describe('DI handshake carries no Claude-shaped capability', () => {
  it('no hooksStatus, no hooksConsentRequest, no provider tool lists, hooks reported off', () => {
    const store = new AgentStateStore();
    store.setAdapter(new FileStateAdapter({ namespace: 'standalone' }));
    const sent: Array<Record<string, unknown>> = [];
    handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'x',
    });
    const types = sent.map((m) => m.type);
    expect(types).not.toContain('hooksStatus');
    expect(types).not.toContain('hooksConsentRequest');
    expect(sent.find((m) => m.type === 'providerCapabilities')).toEqual({
      type: 'providerCapabilities',
      readingTools: [],
      subagentToolNames: [],
    });
    const settings = sent.find((m) => m.type === 'settingsLoaded');
    expect(settings).toMatchObject({
      hooksEnabled: false,
      watchAllSessions: false,
      externalAssetDirectories: [],
    });
    expect(types).toEqual([
      'providerCapabilities',
      'settingsLoaded',
      'areaMappingsLoaded',
      'existingAgents',
      'layoutLoaded',
    ]);
  });
});

describe('DI module graph is free of provider / runtime / scanner / consent code (by construction)', () => {
  /** Follow every VALUE import (type-only imports are erased) from the DI entry. */
  function valueImportClosure(entry: string): string[] {
    const seen = new Set<string>();
    const queue = [entry];
    while (queue.length > 0) {
      const file = queue.pop()!;
      if (seen.has(file)) continue;
      seen.add(file);
      const source = fs.readFileSync(file, 'utf-8');
      for (const match of source.matchAll(/^import\s+(?!type\b)[^;]*?from\s+'(\.[^']+)';/gms)) {
        const target = path.resolve(path.dirname(file), match[1].replace(/\.js$/, '.ts'));
        if (fs.existsSync(target)) queue.push(target);
      }
    }
    return [...seen].map((f) => path.relative(SRC, f)).sort();
  }

  it('the DI entry reaches none of the forbidden modules', () => {
    const closure = valueImportClosure(path.join(SRC, 'di/diCli.ts'));
    const forbidden = closure.filter(
      (f) =>
        f.startsWith('providers/') ||
        /^(agentRuntime|fileWatcher|hookEventHandler|transcriptParser|timerManager|clientMessageHandler|server|httpServer|cli)\.ts$/.test(
          f,
        ),
    );
    expect(forbidden).toEqual([]);
    expect(closure).toContain('di/diHttpServer.ts');
    expect(closure).toContain('socketPipe.ts');
  });
});

describe('write-bearing DI messages: malformed or oversized input is REFUSED, nothing written', () => {
  const VALID = { version: 1, cols: 2, rows: 1, tiles: [1, 1], furniture: [] };
  const handle = (msg: Record<string, unknown>) => {
    const store = new AgentStateStore();
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    const spies = [vi.spyOn(adapter, 'setSetting'), vi.spyOn(adapter, 'saveSeats')];
    store.setAdapter(adapter);
    const send = vi.fn();
    const disposition = handleDiClientMessage(msg, send, {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'test',
    });
    return { disposition, send, spies };
  };

  it('a valid layout is written under the DI root', () => {
    expect(handle({ type: 'saveLayout', layout: VALID }).disposition).toBe('handled');
    expect(treeOf(sandbox)).toContain(path.join('di-root', 'layout.json'));
  });

  const badLayouts: Record<string, unknown> = {
    'not an object': [1, 2],
    'unknown field': { ...VALID, extra: true },
    'version 2': { ...VALID, version: 2 },
    'cols 65': { ...VALID, cols: 65, tiles: new Array(65).fill(1) },
    'tiles length mismatch': { ...VALID, tiles: [1] },
    'tile value 999': { ...VALID, tiles: [1, 999] },
    'furniture item extra key': {
      ...VALID,
      furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: 0, script: 'x' }],
    },
    'furniture uid too long': {
      ...VALID,
      furniture: [{ uid: 'u'.repeat(200), type: 'DESK_FRONT', col: 0, row: 0 }],
    },
    'too much furniture': {
      ...VALID,
      furniture: Array.from({ length: 4_097 }, (_, i) => ({
        uid: `f${i}`,
        type: 'X',
        col: 0,
        row: 0,
      })),
    },
    'tileColors wrong length': { ...VALID, tileColors: [null] },
    'colour component not finite': {
      ...VALID,
      tileColors: [null, { h: 1, s: 1, b: 1, c: Number.NaN }],
    },
    'area colour not hex': { ...VALID, areas: [{ label: 'A', color: 'red' }] },
    'areaTiles label not defined': { ...VALID, areas: [], areaTiles: ['A', null] },
  };
  for (const [name, layout] of Object.entries(badLayouts)) {
    it(`saveLayout REFUSED: ${name}`, () => {
      const { disposition } = handle({ type: 'saveLayout', layout });
      expect(typeof disposition).toBe('object');
      expect(treeOf(sandbox)).toEqual(['home']); // nothing written, anywhere
    });
  }

  it('saveAgentSeats: valid accepted; malformed REFUSED with no adapter write', () => {
    expect(
      handle({ type: 'saveAgentSeats', seats: { 7: { palette: 2, hueShift: 45, seatId: 's' } } })
        .disposition,
    ).toBe('handled');
    for (const seats of [
      [1],
      { abc: {} },
      { 7: { palette: 65 } },
      { 7: { seatId: 'x'.repeat(200) } },
      { 7: { palette: 1, extra: 1 } },
      Object.fromEntries(Array.from({ length: 1_025 }, (_, i) => [String(i), {}])),
    ]) {
      const { disposition, spies } = handle({ type: 'saveAgentSeats', seats });
      expect(typeof disposition).toBe('object');
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    }
  });

  it('saveAgentSeats as getPersistableSeats builds it — an UNSEATED character (seatId null) — is ACCEPTED and round-trips', () => {
    // OfficeState.getPersistableSeats(): every non-subagent character, seatId string | null.
    const seats = {
      1: { palette: 0, hueShift: 0, seatId: 'di-band-0-pod-0-seat-a' },
      2: { palette: 3, hueShift: 135, seatId: null },
    };
    const store = new AgentStateStore();
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    store.setAdapter(adapter);
    const disposition = handleDiClientMessage({ type: 'saveAgentSeats', seats }, vi.fn(), {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'test',
    });
    expect(disposition).toBe('handled'); // the WHOLE message, not refused
    expect(adapter.loadSeats()).toEqual({
      '1': { palette: 0, hueShift: 0, seatId: 'di-band-0-pod-0-seat-a' },
      '2': { palette: 3, hueShift: 135, seatId: null },
    });
  });

  it('preferences: a non-boolean flag or an oversized version is REFUSED, no setting written', () => {
    for (const msg of [
      { type: 'setSoundEnabled', enabled: 'yes' },
      { type: 'setShowAreas', enabled: 1 },
      { type: 'setLastSeenVersion', version: 'v'.repeat(1_000) },
      { type: 'setLastSeenVersion', version: 3 },
    ]) {
      const { disposition, spies } = handle(msg);
      expect(typeof disposition).toBe('object');
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    }
  });

  it('the bounds are consistent: a MAXIMAL legitimate 64x64 layout validates AND fits the WS payload cap', () => {
    const cells = 64 * 64;
    const color = { h: 214, s: 30, b: -100, c: -55, colorize: true };
    const layout = {
      version: 1,
      cols: 64,
      rows: 64,
      tiles: new Array(cells).fill(7),
      furniture: Array.from({ length: 600 }, (_, i) => ({
        uid: `f-${i}-0123456789abcdef`,
        type: 'WOODEN_CHAIR_SIDE:left',
        col: i % 64,
        row: Math.floor(i / 64),
        color,
      })),
      tileColors: new Array(cells).fill(color),
      layoutRevision: 3,
      carpetTiles: new Array(cells).fill({ variant: 2, color, accentColor: color, order: 1 }),
      /* eslint-disable pixel-agents/no-inline-colors -- area colour as layout test data */
      areas: [{ label: 'Mission area 01', color: '#336699' }],
      /* eslint-enable pixel-agents/no-inline-colors */
      areaTiles: new Array(cells).fill('Mission area 01'),
    };
    expect(validateDiLayout(layout)).toBeNull();
    const frame = JSON.stringify({ type: 'saveLayout', layout });
    expect(Buffer.byteLength(frame)).toBeLessThan(DI_WS_MAX_PAYLOAD_BYTES);
  });
});

describe('a FRESH DI office shows its Mission zones by default; the user choice persists after', () => {
  it('no config yet -> Show Areas recorded ON once; an existing config is never touched', () => {
    const fresh = { setSetting: vi.fn() };
    initDiDefaults(fresh, false);
    expect(fresh.setSetting).toHaveBeenCalledWith('pixel-agents.showAreas', true);
    const existing = { setSetting: vi.fn() };
    initDiDefaults(existing, true);
    expect(existing.setSetting).not.toHaveBeenCalled();
  });

  it('OBSERVED CAUSE of the log-14 failure: on a fresh root getSetting IGNORES its default (the config default is false)', () => {
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    expect(adapter.getSetting('pixel-agents.showAreas', true)).toBe(false); // why a handshake default could not work
    initDiDefaults(adapter, false);
    expect(adapter.getSetting('pixel-agents.showAreas', false)).toBe(true); // recorded once at creation
  });
});
