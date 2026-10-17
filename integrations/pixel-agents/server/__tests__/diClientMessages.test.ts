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

import type { Shape } from './helpers/layoutCaps.js';
import {
  AREA_COLOUR,
  CAP,
  cellCapReason,
  DIM_SQUARED,
  heavyLabel,
  JUST_OVER_CELLS,
  largestAdmitted,
  LONG_COLOUR,
  maximalLegitimate,
  metropolisShape,
  overBytes,
  TALLEST,
  tilesOnly,
  WIDEST,
} from './helpers/layoutCaps.js';

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
const {
  DI_ID_MAX_LENGTH,
  DI_LAYOUT_MAX_CELLS,
  DI_LAYOUT_MAX_DIM,
  DI_LAYOUT_MAX_FURNITURE,
  DI_WS_MAX_PAYLOAD_BYTES,
} = await import('../src/constants.js');
/** Near-cap layouts take ~0.3-0.6 s per check (structure + two UTF-8 byte counts). */
const HEAVY_MS = 120_000;

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
    expect(variants).toHaveLength(23); // + saveAmbientState (M3 S4, DI only)
    const declared = new Set<string>([...DI_HANDLED_MESSAGE_TYPES, ...DI_INERT_MESSAGE_TYPES]);
    expect(declared.size).toBe(23);
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
      'ambientStateLoaded',
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
    'cols 65 with a short tiles array': { ...VALID, cols: 65, tiles: new Array(64).fill(1) },
    [`cols ${DI_LAYOUT_MAX_DIM + 1} (over the per-axis world ceiling)`]: {
      ...VALID,
      cols: DI_LAYOUT_MAX_DIM + 1,
      rows: 1,
      tiles: new Array(DI_LAYOUT_MAX_DIM + 1).fill(1),
    },
    [`rows ${DI_LAYOUT_MAX_DIM + 1} (over the per-axis world ceiling)`]: {
      ...VALID,
      cols: 1,
      rows: DI_LAYOUT_MAX_DIM + 1,
      tiles: new Array(DI_LAYOUT_MAX_DIM + 1).fill(1),
    },
    [`${JUST_OVER_CELLS.cols} x ${JUST_OVER_CELLS.rows} (each axis within the ceiling, cells over the total-cell cap)`]:
      tilesOnly(JUST_OVER_CELLS),
    'cols 0': { ...VALID, cols: 0, tiles: [] },
    'large world, tiles one short': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112 - 1).fill(1),
    },
    'large world, tileColors one long': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      tileColors: new Array(176 * 112 + 1).fill(null),
    },
    'large world, carpetTiles wrong length': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      carpetTiles: new Array(64 * 64).fill(null),
    },
    'large world, areaTiles wrong length': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      areas: [],
      areaTiles: new Array(176 * 111).fill(null),
    },
    'furniture col beyond twice the world ceiling': {
      ...VALID,
      furniture: [{ uid: 'a', type: 'X', col: 2 * DI_LAYOUT_MAX_DIM + 1, row: 0 }],
    },
    'pet with an unknown key': { ...VALID, pets: [{ id: 'p', petType: 0, name: 'x' }] },
    'pet type not an integer': { ...VALID, pets: [{ id: 'p', petType: 0.5 }] },
    'tiles length mismatch': { ...VALID, tiles: [1] },
    'tile value 999': { ...VALID, tiles: [1, 999] },
    'furniture item extra key': {
      ...VALID,
      furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: 0, script: 'x' }],
    },
    'furniture uid too long': {
      ...VALID,
      furniture: [{ uid: 'u'.repeat(DI_ID_MAX_LENGTH + 1), type: 'DESK_FRONT', col: 0, row: 0 }],
    },
    'too much furniture': {
      ...VALID,
      furniture: Array.from({ length: DI_LAYOUT_MAX_FURNITURE + 1 }, (_, i) => ({
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

  it('a layout wider than the former 64 ceiling is now ACCEPTED (65 x 1)', () => {
    expect(
      validateDiLayout({ ...VALID, cols: 65, rows: 1, tiles: new Array(65).fill(1) }),
    ).toBeNull();
  });

  it('the per-axis ceiling is 384 and the total-cell cap 110,400 (independent): DIM x DIM is REFUSED for its cells, cap-size layouts validate', () => {
    expect(DI_LAYOUT_MAX_DIM).toBe(384);
    expect(DI_LAYOUT_MAX_CELLS).toBe(110_400);
    // independent of the axis bound: DIM x DIM is NOT admitted
    expect(DI_LAYOUT_MAX_CELLS).toBeLessThan(DI_LAYOUT_MAX_DIM * DI_LAYOUT_MAX_DIM);
    const full = ({ cols, rows }: Shape) => {
      const cells = cols * rows;
      return {
        version: 1,
        cols,
        rows,
        tiles: new Array(cells).fill(1),
        furniture: [{ uid: 'wall-item', type: 'CLOCK', col: 24, row: -1 }], // negative rows stay legal
        tileColors: new Array(cells).fill(null),
        carpetTiles: new Array(cells).fill(null),
        areas: [],
        areaTiles: new Array(cells).fill(null),
      };
    };
    expect(validateDiLayout(full(DIM_SQUARED))).toBe(
      `layout cols x rows is ${DI_LAYOUT_MAX_DIM * DI_LAYOUT_MAX_DIM} cells, over ${DI_LAYOUT_MAX_CELLS}`,
    );
    expect(validateDiLayout(full(JUST_OVER_CELLS))).toBe(cellCapReason(JUST_OVER_CELLS));
    // exactly the cell cap, and the widest / tallest grids within it, all validate
    expect(CAP.cols * CAP.rows).toBe(DI_LAYOUT_MAX_CELLS);
    for (const shape of [CAP, WIDEST, TALLEST, metropolisShape()])
      expect(validateDiLayout(full(shape)), `${shape.cols} x ${shape.rows}`).toBeNull();
  });

  for (const shape of [CAP, WIDEST, metropolisShape()]) {
    it(
      `the bounds are consistent: a MAXIMAL legitimate ${shape.cols}x${shape.rows} layout (every structural cap at once) validates AND fits the WS payload cap with ~2x headroom`,
      () => {
        const layout = maximalLegitimate(shape);
        expect(validateDiLayout(layout)).toBeNull();
        const frame = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout }));
        expect(frame).toBeLessThan(DI_WS_MAX_PAYLOAD_BYTES);
        // ~2x headroom over the maximal legitimate frame, not an accidental fit.
        expect(frame * 2).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES * 1.05);
      },
      HEAVY_MS,
    );
  }

  // F2 (review round 1): ids and labels are bounded in CHARACTERS and areaTiles carries a label per
  // cell, so UTF-8 text can make a structurally valid layout too big to TRANSPORT. Admission must
  // measure UTF-8 bytes, so such a layout is refused BY ADMISSION, never reaching the transport
  // ceiling (1009). At the raised caps a 3-byte label on every cell no longer crosses the ceiling on
  // its own (asserted below), and since the carpeted-cell cap (LAYOUT_MAX_CARPET_CELLS) even every
  // colour at its longest spelling is not enough: the furniture ids are also made of characters
  // JSON escapes to six bytes. The three-byte labels still make the CHARACTER count fit while the
  // UTF-8 BYTE count does not — the property under test.
  const FRAME_OVER = new RegExp(
    `save frame is \\d+ bytes \\(UTF-8\\), over ${DI_WS_MAX_PAYLOAD_BYTES}$`,
  );
  /** 128 three-byte characters (384 UTF-8 bytes), within the 128-char id bound; unique per k. */
  const cjkLabel = (k: number) =>
    String.fromCharCode(0x4e00 + k) + '界'.repeat(DI_ID_MAX_LENGTH - 1);
  it(
    'a UTF-8 maximal layout (structurally valid) is REFUSED by admission for its BYTES',
    () => {
      const base = maximalLegitimate(CAP, { label: cjkLabel, colour: LONG_COLOUR });
      const layout = {
        ...base,
        furniture: (base.furniture as Array<Record<string, unknown>>).map((f, k) => ({
          ...f,
          uid: heavyLabel(k),
          type: heavyLabel(k),
        })),
      };
      const text = JSON.stringify({ type: 'saveLayout', layout });
      // it would FIT if characters were counted; in UTF-8 bytes the transport would close 1009
      expect(text.length).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
      expect(Buffer.byteLength(text)).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(layout)).toMatch(FRAME_OVER);
      // and the six-byte-escaped fixture every entry path refuses is refused here too
      const escaped = overBytes();
      expect(
        Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: escaped })),
      ).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(escaped)).toMatch(FRAME_OVER);
    },
    HEAVY_MS,
  );

  it(
    'a 3-byte label on every cell of the maximal layout is no longer enough to cross the raised ceiling: ADMITTED, and it fits',
    () => {
      const layout = maximalLegitimate(CAP, { label: cjkLabel });
      expect(validateDiLayout(layout)).toBeNull();
      const frame = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout }));
      expect(frame).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
    },
    HEAVY_MS,
  );

  it(
    'admission == transport: at the byte boundary, admitted layouts fit and the next one is refused',
    () => {
      // Grow a heavy (six-byte-escaped) area label over the cells of the maximal cap-size layout
      // until the frame crosses the ceiling: the last admitted layout's frame fits, the first
      // refused one's does not (bytes, not structure). Binary search, so no count is hand-tuned.
      const label = heavyLabel();
      const base = maximalLegitimate(CAP);
      const cells = CAP.cols * CAP.rows;
      const labelled = (n: number) => ({
        ...base,
        areas: [
          { label, color: AREA_COLOUR },
          { label: 'a', color: AREA_COLOUR },
        ],
        areaTiles: Array.from({ length: cells }, (_, i) => (i < n ? label : 'a')),
      });
      const lo = largestAdmitted(0, cells, (n) => validateDiLayout(labelled(n)) === null);
      const hi = lo + 1;
      const fits = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: labelled(lo) }));
      const over = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: labelled(hi) }));
      expect(validateDiLayout(labelled(lo))).toBeNull();
      expect(fits).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(labelled(hi))).toMatch(FRAME_OVER);
      expect(over).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
    },
    HEAVY_MS,
  );

  it('the bounds are consistent: a MAXIMAL legitimate 64x64 layout (the former ceiling, every other cap at its maximum) validates AND fits the WS payload cap', () => {
    const layout = maximalLegitimate({ cols: 64, rows: 64 });
    expect((layout.furniture as unknown[]).length).toBe(DI_LAYOUT_MAX_FURNITURE);
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
