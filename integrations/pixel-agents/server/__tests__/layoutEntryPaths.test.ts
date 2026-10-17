/**
 * Every SERVER path a layout enters through is bounded by the ONE shared check
 * (core/src/layoutLimits.ts): the layout file read, the file watcher, the DI handshake that
 * sends the file to the browser, the DI saveLayout refusal (which re-sends the persisted
 * layout so the client never keeps showing what was not saved), the persistence sink and the
 * VS Code import. Each refusal class — per-axis ceiling, the INDEPENDENT total-cell cap, the
 * furniture / pet / area / position caps, malformed shapes, a `home` region that is malformed or
 * off the grid, and a structurally valid layout over the BYTE ceiling — is proven at each of those
 * paths; layouts AT the caps (a home up to the grid's own bounds included) are admitted. Oversized
 * bytes are refused before the file is read; legacy layouts the webview migrates still load.
 * Every bound below is derived from the imported constants, never copied.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Layout } from './helpers/layoutCaps.js';
import {
  AREA_COLOUR,
  CAP,
  carpetVariants,
  cellCapReason,
  DIM_SQUARED,
  furnitureVariants,
  heavyLabel,
  JUST_OVER_CELLS,
  largestAdmitted,
  maximalLegitimate,
  metropolisShape,
  overBytes,
  TALLEST,
  tilesOnly,
  tileVariants,
  WIDEST,
} from './helpers/layoutCaps.js';

let sandbox: string;
let fakeHome: string;

vi.mock('os', async () => {
  const actual = await vi.importActual<typeof import('os')>('os');
  return { ...actual, homedir: () => fakeHome };
});

const { resetStateRootForTests, setStateRoot } = await import('../src/stateRoot.js');
const { importLayoutFile, readLayoutFromFile, watchLayoutFile, writeLayoutToFile } =
  await import('../src/layoutPersistence.js');
const { AgentStateStore } = await import('../src/agentStateStore.js');
const { FileStateAdapter } = await import('../src/fileStateAdapter.js');
const { handleDiClientMessage } = await import('../src/di/diClientMessages.js');
const {
  admitLayout,
  checkLayout,
  checkLayoutForm,
  layoutByteSizes,
  LAYOUT_MAX_AREAS,
  LAYOUT_MAX_CARPET_CELLS,
  LAYOUT_MAX_CARPET_COLOUR_VARIANTS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FILE_BYTES,
  LAYOUT_MAX_FRAME_BYTES,
  LAYOUT_MAX_FURNITURE,
  LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS,
  LAYOUT_MAX_PETS,
  LAYOUT_MAX_TILE_COLOUR_VARIANTS,
  LAYOUT_POSITION_BOUND,
} = await import('../../core/src/layoutLimits.js');
const { LAYOUT_FILE_POLL_INTERVAL_MS } = await import('../src/constants.js');

const REPO = path.join(__dirname, '../..');
const VALID = { version: 1, cols: 2, rows: 1, tiles: [1, 1], furniture: [] };
const DEFAULT_LAYOUT = { version: 1, cols: 1, rows: 1, tiles: [1], furniture: [] };
/** Near-cap layouts take ~0.3-0.6 s per check (structure + two UTF-8 byte counts). */
const HEAVY_MS = 120_000;
const METRO = metropolisShape();

const furnitureItems = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ uid: `f${i}`, type: 'DESK_FRONT', col: 0, row: 0 }));
const petList = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, petType: 0 }));
const areaList = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ label: `A${i}`, color: AREA_COLOUR }));

/** One bad layout per refusal class the entry paths must reject. */
const BAD: Record<string, unknown> = {
  // (the review case — formerly 193 x 1 — re-derived: one column past the per-axis ceiling)
  'oversized dims (cols over the ceiling)': {
    ...VALID,
    cols: LAYOUT_MAX_DIM + 1,
    tiles: new Array(LAYOUT_MAX_DIM + 1).fill(1),
  },
  'oversized dims (rows over the ceiling)': {
    ...VALID,
    cols: 1,
    rows: LAYOUT_MAX_DIM + 1,
    tiles: new Array(LAYOUT_MAX_DIM + 1).fill(1),
  },
  'over the total-cell cap (each axis within LAYOUT_MAX_DIM)': tilesOnly(JUST_OVER_CELLS),
  'DIM x DIM (both axes AT LAYOUT_MAX_DIM, cells over the cap)': tilesOnly(DIM_SQUARED),
  'furniture over LAYOUT_MAX_FURNITURE': {
    ...VALID,
    furniture: furnitureItems(LAYOUT_MAX_FURNITURE + 1),
  },
  'pets over LAYOUT_MAX_PETS': { ...VALID, pets: petList(LAYOUT_MAX_PETS + 1) },
  'areas over LAYOUT_MAX_AREAS': { ...VALID, areas: areaList(LAYOUT_MAX_AREAS + 1) },
  // distinct colour variants: each one a renderer cache entry (see layoutLimits.ts)
  'tile colour variants over LAYOUT_MAX_TILE_COLOUR_VARIANTS': tileVariants(
    LAYOUT_MAX_TILE_COLOUR_VARIANTS + 1,
  ),
  'carpet colour variants over LAYOUT_MAX_CARPET_COLOUR_VARIANTS': carpetVariants(
    LAYOUT_MAX_CARPET_COLOUR_VARIANTS + 1,
  ),
  'furniture colour variants over LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS': furnitureVariants(
    LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS + 1,
  ),
  'position over LAYOUT_POSITION_BOUND': {
    ...VALID,
    furniture: [{ uid: 'a', type: 'DESK_FRONT', col: LAYOUT_POSITION_BOUND + 1, row: 0 }],
  },
  'negative position over LAYOUT_POSITION_BOUND': {
    ...VALID,
    furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: -(LAYOUT_POSITION_BOUND + 1) }],
  },
  'mismatched parallel array (tiles)': { ...VALID, tiles: [1] },
  'mismatched parallel array (carpetTiles)': { ...VALID, carpetTiles: [null] },
  'mismatched parallel array (areaTiles)': { ...VALID, areas: [], areaTiles: [null, null, null] },
  'unknown top-level field': { ...VALID, script: 'x' },
  'unknown furniture field': {
    ...VALID,
    furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: 0, onload: 'x' }],
  },
  'malformed colour': { ...VALID, tileColors: [null, { h: 'red', s: 0, b: 0, c: 0 }] },
  'malformed pet': { ...VALID, pets: [{ id: '', petType: 0 }] },
  'malformed area': { ...VALID, areas: [{ label: 'A', color: 'red' }] },
  // The explicit HOME region (worldViews.officeRegion) is bounds-relevant: an integer tile
  // rectangle 0 <= c0 < c1 <= cols, 0 <= r0 < r1 <= rows, with exactly the keys c0/r0/c1/r1.
  'home with an extra key': { ...VALID, home: { c0: 0, r0: 0, c1: 1, r1: 1, onload: 'x' } },
  'home missing a key': { ...VALID, home: { c0: 0, r0: 0, c1: 1 } },
  'home not an object': { ...VALID, home: [0, 0, 1, 1] },
  'home non-integer': { ...VALID, home: { c0: 0.5, r0: 0, c1: 1, r1: 1 } },
  'home non-number': { ...VALID, home: { c0: '0', r0: 0, c1: 1, r1: 1 } },
  'home empty (c1 <= c0)': { ...VALID, home: { c0: 1, r0: 0, c1: 1, r1: 1 } },
  'home inverted (r1 <= r0)': { ...VALID, home: { c0: 0, r0: 0, c1: 1, r1: 0 } },
  'home outside the grid (c1 past cols)': {
    ...VALID,
    home: { c0: 0, r0: 0, c1: VALID.cols + 1, r1: VALID.rows },
  },
  'home outside the grid (r1 past rows)': {
    ...VALID,
    home: { c0: 0, r0: 0, c1: VALID.cols, r1: VALID.rows + 1 },
  },
  'home outside the grid (negative c0)': { ...VALID, home: { c0: -1, r0: 0, c1: 1, r1: 1 } },
};

/** Every malformed-home fixture above: refused for the home check itself. */
const HOME_REASONS: Record<string, string> = Object.fromEntries(
  Object.keys(BAD)
    .filter((name) => name.startsWith('home '))
    .map((name) => [name, 'layout home malformed']),
);

/** The reason each CAP class must be refused for — its OWN cap, not an incidental flaw. */
const CAP_REASONS: Record<string, string> = {
  'oversized dims (cols over the ceiling)': `layout cols/rows outside 1..${LAYOUT_MAX_DIM}`,
  'oversized dims (rows over the ceiling)': `layout cols/rows outside 1..${LAYOUT_MAX_DIM}`,
  'over the total-cell cap (each axis within LAYOUT_MAX_DIM)': cellCapReason(JUST_OVER_CELLS),
  'DIM x DIM (both axes AT LAYOUT_MAX_DIM, cells over the cap)': cellCapReason(DIM_SQUARED),
  'furniture over LAYOUT_MAX_FURNITURE': 'layout furniture missing or too long',
  'pets over LAYOUT_MAX_PETS': 'layout pets malformed',
  'areas over LAYOUT_MAX_AREAS': 'layout areas malformed',
  'tile colour variants over LAYOUT_MAX_TILE_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_TILE_COLOUR_VARIANTS} distinct tile colour variants`,
  'carpet colour variants over LAYOUT_MAX_CARPET_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_CARPET_COLOUR_VARIANTS} distinct carpet colour variants`,
  'furniture colour variants over LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS} distinct furniture colour variants`,
  'position over LAYOUT_POSITION_BOUND': 'layout furniture item malformed',
  'negative position over LAYOUT_POSITION_BOUND': 'layout furniture item malformed',
  ...HOME_REASONS,
};
/** The refusal every path must report for a BAD layout: the cap's own reason, else the shared
 *  check's (so a path can never refuse for a different reason than the ONE definition). */
const reasonFor = (name: string): string => {
  const reason = CAP_REASONS[name] ?? checkLayout(BAD[name], { legacy: true });
  if (reason === null) throw new Error(`BAD fixture ${name} is admissible`);
  return reason;
};

/** Exactly AT each cap: admitted everywhere (no off-by-one over-refusal). */
const AT_CAP: Record<string, () => Layout> = {
  [`exactly the cell cap (${CAP.cols} x ${CAP.rows}), tiles only`]: () => tilesOnly(CAP),
  [`the widest grid within the cell cap (${WIDEST.cols} x ${WIDEST.rows}), tiles only`]: () =>
    tilesOnly(WIDEST),
  [`the tallest grid within the cell cap (${TALLEST.cols} x ${TALLEST.rows}), tiles only`]: () =>
    tilesOnly(TALLEST),
  [`the connected metropolis grid (${METRO.cols} x ${METRO.rows}), tiles only`]: () =>
    tilesOnly(METRO),
  'exactly LAYOUT_MAX_FURNITURE furniture': () => ({
    ...VALID,
    furniture: furnitureItems(LAYOUT_MAX_FURNITURE),
  }),
  'exactly LAYOUT_MAX_PETS pets': () => ({ ...VALID, pets: petList(LAYOUT_MAX_PETS) }),
  'exactly LAYOUT_MAX_AREAS areas': () => ({ ...VALID, areas: areaList(LAYOUT_MAX_AREAS) }),
  'exactly LAYOUT_MAX_TILE_COLOUR_VARIANTS tile colour variants': () =>
    tileVariants(LAYOUT_MAX_TILE_COLOUR_VARIANTS),
  'exactly LAYOUT_MAX_CARPET_COLOUR_VARIANTS carpet colour variants': () =>
    carpetVariants(LAYOUT_MAX_CARPET_COLOUR_VARIANTS),
  'exactly LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS furniture colour variants': () =>
    furnitureVariants(LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS),
  'positions exactly at +/- LAYOUT_POSITION_BOUND': () => ({
    ...VALID,
    furniture: [
      { uid: 'a', type: 'DESK_FRONT', col: LAYOUT_POSITION_BOUND, row: -LAYOUT_POSITION_BOUND },
      { uid: 'b', type: 'DESK_FRONT', col: -LAYOUT_POSITION_BOUND, row: LAYOUT_POSITION_BOUND },
    ],
  }),
  'the MAXIMAL legitimate layout at the cell cap (every structural cap at once)': () =>
    maximalLegitimate(),
  'a home exactly the whole grid (at its bounds)': () => ({
    ...VALID,
    home: { c0: 0, r0: 0, c1: VALID.cols, r1: VALID.rows },
  }),
  [`a 1 x 1 home in the far corner of the metropolis grid (${METRO.cols} x ${METRO.rows})`]:
    () => ({
      ...tilesOnly(METRO),
      home: { c0: METRO.cols - 1, r0: METRO.rows - 1, c1: METRO.cols, r1: METRO.rows },
    }),
  // the shipped world's home: the forest's 64 x 64 palace block (world data, not a bound)
  'the metropolis grid with its palace home': () => ({
    ...tilesOnly(METRO),
    home: { c0: 56, r0: 40, c1: 120, r1: 104 },
  }),
};

/** A frame over the ceiling, refused BY BYTES (the last check: all structure passed). */
const FRAME_REFUSAL = new RegExp(
  `^layout save frame is \\d+ bytes \\(UTF-8\\), over ${LAYOUT_MAX_FRAME_BYTES}$`,
);

const layoutPath = () => path.join(sandbox, 'root', 'layout.json');
const writeRaw = (text: string) => {
  fs.mkdirSync(path.dirname(layoutPath()), { recursive: true });
  fs.writeFileSync(layoutPath(), text);
};
/** A file one byte over the ceiling (sparse: the size is what is checked, before any read). */
const writeOversizedFile = () => {
  writeRaw('');
  fs.truncateSync(layoutPath(), LAYOUT_MAX_FILE_BYTES + 1);
};
/** Big layouts compare by their serialization (a deep-equality diff of 81,920 cells is useless). */
const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const silenceLogs = () => {
  vi.spyOn(console, 'log').mockImplementation(() => {});
  return vi.spyOn(console, 'error').mockImplementation(() => {});
};
const errorLines = (spy: ReturnType<typeof silenceLogs>) => spy.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'layout-paths-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);
  resetStateRootForTests();
  setStateRoot(path.join(sandbox, 'root'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

/** The DI handler, as the server runs it: what it did, and every layout it sent the client. */
const DI_CACHE = {
  characters: null,
  pets: null,
  floorTiles: null,
  wallTiles: null,
  carpetTiles: null,
  furniture: null,
  defaultLayout: DEFAULT_LAYOUT,
};
const runDi = (msg: Record<string, unknown>) => {
  const store = new AgentStateStore();
  store.setAdapter(new FileStateAdapter({ namespace: 'standalone' }));
  const sent: Array<Record<string, unknown>> = [];
  const disposition = handleDiClientMessage(msg, (m) => sent.push(m), {
    store,
    cache: DI_CACHE,
    extensionVersion: 'test',
  });
  const layouts = sent.filter((m) => m.type === 'layoutLoaded').map((m) => m.layout);
  return { disposition, layouts };
};

describe('the refusal fixtures are refused for their OWN cap, and AT each cap admitted', () => {
  for (const [name, reason] of Object.entries(CAP_REASONS)) {
    it(`${name}: refused with "${reason}" in both modes`, () => {
      expect(checkLayout(BAD[name], { legacy: true })).toBe(reason);
      expect(checkLayout(BAD[name])).toBe(reason);
    });
  }
  for (const [name, make] of Object.entries(AT_CAP)) {
    it(
      `${name}: admitted`,
      () => {
        expect(checkLayout(make(), { legacy: true })).toBeNull();
      },
      HEAVY_MS,
    );
  }
});

describe('readLayoutFromFile: bounded before read, checked after parse', () => {
  it('an oversized file is refused by its byte size, before it is read or parsed', () => {
    writeOversizedFile();
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(readLayoutFromFile()).toBeNull();
    // the SIZE refusal (stat), not a parse failure: the file was never read
    expect(errors.mock.calls.map((c) => String(c[0]))).toEqual([
      `[Pixel Agents] Refusing layout file: ${LAYOUT_MAX_FILE_BYTES + 1} bytes exceeds ${LAYOUT_MAX_FILE_BYTES}`,
    ]);
    errors.mockRestore();
  });
  for (const [name, layout] of Object.entries(BAD)) {
    it(`refused: ${name}`, () => {
      writeRaw(JSON.stringify(layout, null, 2));
      const errors = silenceLogs();
      expect(readLayoutFromFile()).toBeNull();
      expect(errorLines(errors)).toEqual([
        `[Pixel Agents] Refusing layout file: ${reasonFor(name)}`,
      ]);
    });
  }
  it('not JSON is refused', () => {
    writeRaw('{ nope');
    expect(readLayoutFromFile()).toBeNull();
  });
});

describe('legacy and shipped layouts still load through the bounded reader', () => {
  const legacy: Record<string, unknown> = {
    'old VOID value 8 (pre-revision layout)': {
      version: 1,
      cols: 2,
      rows: 1,
      tiles: [8, 1],
      furniture: [],
    },
    'legacy tile types 1-4, no tileColors': {
      version: 1,
      cols: 4,
      rows: 1,
      tiles: [1, 2, 3, 4],
      furniture: [],
    },
    'legacy furniture type strings': {
      version: 1,
      cols: 2,
      rows: 1,
      tiles: [1, 1],
      furniture: [
        { uid: 'a', type: 'desk', col: 0, row: 0 },
        { uid: 'b', type: 'cooler', col: 1, row: 0 },
      ],
    },
    'tileColors of the wrong length (migration regenerates it)': {
      version: 1,
      cols: 2,
      rows: 1,
      tiles: [1, 1],
      tileColors: [null],
      furniture: [],
    },
  };
  for (const [name, layout] of Object.entries(legacy)) {
    it(`accepted: ${name}`, () => {
      writeRaw(JSON.stringify(layout, null, 2));
      expect(readLayoutFromFile()).toEqual(layout);
    });
  }
  for (const rel of [
    'webview-ui/public/assets/default-layout-1.json',
    'packaging/di/worlds/expanded-office.json',
    'packaging/di/worlds/vast-forest.json',
    'packaging/di/worlds/metropolis.json',
    'packaging/di/worlds/living-metropolis.json',
  ]) {
    it(`accepted: ${rel}`, () => {
      writeRaw(fs.readFileSync(path.join(REPO, rel), 'utf-8'));
      expect(readLayoutFromFile()).not.toBeNull();
    });
  }
  it('the living metropolis (M3, 352 x 312 = 109,824 cells) persists: saved by the sink, read back identical', () => {
    const living = JSON.parse(
      fs.readFileSync(path.join(REPO, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
    ) as Record<string, unknown>;
    expect((living.cols as number) * (living.rows as number)).toBe(109_824);
    expect(writeLayoutToFile(living)).toBeNull();
    expect(sameJson(readLayoutFromFile(), living)).toBe(true);
  });
});

describe('AT the caps: admitted by the file read and by DI saveLayout (no over-refusal)', () => {
  for (const [name, make] of Object.entries(AT_CAP)) {
    it(
      `read path accepts: ${name}`,
      () => {
        const layout = make();
        writeRaw(JSON.stringify(layout, null, 2));
        expect(sameJson(readLayoutFromFile(), layout)).toBe(true);
      },
      HEAVY_MS,
    );
    it(
      `DI saveLayout accepts and persists: ${name}`,
      () => {
        const layout = make();
        const { disposition, layouts } = runDi({ type: 'saveLayout', layout });
        expect(disposition).toBe('handled');
        expect(layouts).toEqual([]); // an accepted save is not answered with a re-send
        expect(sameJson(readLayoutFromFile(), layout)).toBe(true);
      },
      HEAVY_MS,
    );
  }
});

describe('watchLayoutFile: an external change is forwarded only when it passes the bounds', () => {
  it(
    'oversized and malformed external writes are never forwarded; a valid one is',
    async () => {
      writeLayoutToFile(VALID);
      const seen: unknown[] = [];
      const watcher = watchLayoutFile((layout) => seen.push(layout));
      const settle = () => new Promise((r) => setTimeout(r, LAYOUT_FILE_POLL_INTERVAL_MS + 300));
      let t = Date.now() / 1000 + 5;
      const bump = () => fs.utimesSync(layoutPath(), t, ++t);
      try {
        writeOversizedFile();
        bump();
        await settle();
        writeRaw(JSON.stringify(BAD['oversized dims (cols over the ceiling)']));
        bump();
        await settle();
        writeRaw(JSON.stringify(BAD['malformed area']));
        bump();
        await settle();
        expect(seen).toEqual([]);
        writeRaw(JSON.stringify({ ...VALID, tiles: [7, 7] }));
        bump();
        await settle();
        expect(seen).toEqual([{ ...VALID, tiles: [7, 7] }]);
      } finally {
        watcher.dispose();
      }
    },
    LAYOUT_FILE_POLL_INTERVAL_MS * 10,
  );

  // Every refusal class, one watcher each. The poll interval is driven by fake timers so each
  // check runs deterministically: the bad write is READ and REFUSED (logged) and never
  // forwarded, and the same watcher then forwards the next valid write (it is alive, not blind).
  const WATCHED: Record<string, () => void> = {
    ...Object.fromEntries(
      Object.entries(BAD).map(([name, layout]) => [
        name,
        () => writeRaw(JSON.stringify(layout, null, 2)),
      ]),
    ),
    'oversized file': writeOversizedFile,
    'over the byte ceiling (structurally valid)': () =>
      writeRaw(JSON.stringify(overBytes(), null, 2)),
  };
  for (const [name, write] of Object.entries(WATCHED)) {
    it(
      `watcher never forwards a ${name} external write; the next valid one is forwarded`,
      () => {
        writeLayoutToFile(VALID);
        vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
        const seen: unknown[] = [];
        const watcher = watchLayoutFile((layout) => seen.push(layout));
        const errors = silenceLogs();
        let t = Date.now() / 1000 + 5;
        const bump = () => fs.utimesSync(layoutPath(), t, ++t);
        try {
          write();
          bump();
          vi.advanceTimersByTime(LAYOUT_FILE_POLL_INTERVAL_MS);
          expect(seen).toEqual([]);
          const refused = errorLines(errors);
          expect(refused).toHaveLength(1);
          expect(refused[0]).toMatch(/^\[Pixel Agents\] Refusing layout file: /);
          if (name in BAD)
            expect(refused[0]).toBe(`[Pixel Agents] Refusing layout file: ${reasonFor(name)}`);
          writeRaw(JSON.stringify({ ...VALID, tiles: [7, 7] }));
          bump();
          vi.advanceTimersByTime(LAYOUT_FILE_POLL_INTERVAL_MS);
          expect(seen).toEqual([{ ...VALID, tiles: [7, 7] }]);
        } finally {
          watcher.dispose();
        }
      },
      HEAVY_MS,
    );
  }
});

describe('DI paths: the handshake and a refused save only ever send a bounded layout', () => {
  for (const [name, layout] of Object.entries({ ...BAD, 'oversized file': null })) {
    it(`handshake never sends a ${name} file to the browser (sends the bundled default)`, () => {
      if (layout === null) writeOversizedFile();
      else writeRaw(JSON.stringify(layout, null, 2));
      silenceLogs();
      expect(runDi({ type: 'webviewReady' }).layouts).toEqual([DEFAULT_LAYOUT]);
    });
  }

  it('handshake sends a valid saved layout unchanged', () => {
    writeLayoutToFile(VALID);
    expect(runDi({ type: 'webviewReady' }).layouts).toEqual([VALID]);
  });

  it('a REFUSED save re-sends the persisted layout, so the client converges on what is saved', () => {
    writeLayoutToFile(VALID);
    const before = fs.readFileSync(layoutPath(), 'utf-8');
    const { disposition, layouts } = runDi({ type: 'saveLayout', layout: BAD['malformed pet'] });
    expect(typeof disposition).toBe('object');
    expect(layouts).toEqual([VALID]);
    expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
  });

  for (const [name, layout] of Object.entries(BAD)) {
    it(`DI saveLayout REFUSES a ${name} layout: persisted file byte-identical, persisted layout re-sent`, () => {
      writeLayoutToFile(VALID);
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      silenceLogs();
      const { disposition, layouts } = runDi({ type: 'saveLayout', layout });
      expect(disposition).toEqual({ refused: CAP_REASONS[name] ?? checkLayout(layout) });
      expect(layouts).toEqual([VALID]);
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    });
  }
});

describe('the persistence sink and the VS Code import: a refused layout never replaces the saved one', () => {
  const sourceFile = () => path.join(sandbox, 'chosen.json');

  it(
    'writeLayoutToFile refuses an over-budget layout and every refusal class; the saved file is untouched',
    () => {
      expect(writeLayoutToFile(VALID)).toBeNull();
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      silenceLogs();
      expect(writeLayoutToFile(overBytes())).toMatch(FRAME_REFUSAL);
      expect(writeLayoutToFile(BAD['malformed area'] as Record<string, unknown>)).toBe(
        'layout area malformed',
      );
      for (const name of Object.keys(BAD))
        expect(writeLayoutToFile(BAD[name] as Record<string, unknown>), name).toBe(reasonFor(name));
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    },
    HEAVY_MS,
  );

  const REFUSED_IMPORTS: Record<string, () => void> = {
    ...Object.fromEntries(
      Object.entries(BAD).map(([name, layout]) => [
        name,
        () => fs.writeFileSync(sourceFile(), JSON.stringify(layout, null, 2)),
      ]),
    ),
    'an oversized file': () => {
      fs.writeFileSync(sourceFile(), '');
      fs.truncateSync(sourceFile(), LAYOUT_MAX_FILE_BYTES + 1);
    },
    'not JSON': () => fs.writeFileSync(sourceFile(), '{ nope'),
    'over the byte ceiling (structurally valid)': () =>
      fs.writeFileSync(sourceFile(), JSON.stringify(overBytes(), null, 2)),
  };
  for (const [name, prepare] of Object.entries(REFUSED_IMPORTS)) {
    it(
      `import REFUSED (${name}): saved layout byte-identical, nothing reported, no watcher mark`,
      () => {
        expect(writeLayoutToFile(VALID)).toBeNull();
        const before = fs.readFileSync(layoutPath(), 'utf-8');
        prepare();
        let marked = 0;
        const result = importLayoutFile(sourceFile(), () => marked++);
        expect('refused' in result).toBe(true);
        if (name in BAD) expect(result).toEqual({ refused: reasonFor(name) });
        expect(marked).toBe(0);
        expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
      },
      HEAVY_MS,
    );
  }

  it('an accepted import is written, marked as our own write, and returned', () => {
    writeLayoutToFile(VALID);
    const imported = JSON.parse(
      fs.readFileSync(path.join(REPO, 'packaging/di/worlds/vast-forest.json'), 'utf-8'),
    );
    fs.writeFileSync(sourceFile(), JSON.stringify(imported));
    let marked = 0;
    const result = importLayoutFile(sourceFile(), () => marked++);
    expect(result).toEqual({ layout: imported });
    expect(marked).toBe(1);
    expect(readLayoutFromFile()).toEqual(imported);
  });
});

describe('over the BYTE ceiling (structurally valid): refused at the sink, the import, DI saveLayout and the file read', () => {
  const sourceFile = () => path.join(sandbox, 'chosen.json');

  it(
    'the fixture: within every structural cap and already migrated, over the frame ceiling, its file under the file ceiling',
    () => {
      const layout = overBytes();
      const { frame, file } = layoutByteSizes(layout);
      expect(frame).toBeGreaterThan(LAYOUT_MAX_FRAME_BYTES);
      expect(file).toBeLessThanOrEqual(LAYOUT_MAX_FILE_BYTES); // so a reader gets past the stat
      // bytes are the LAST check, so this reason means every structural check passed
      expect(checkLayoutForm(layout, { legacy: true })).toMatch(FRAME_REFUSAL);
      expect(checkLayout(layout)).toMatch(FRAME_REFUSAL); // not "after migration": nothing to migrate
    },
    HEAVY_MS,
  );

  it(
    'the sink refuses it; the saved file is byte-identical',
    () => {
      expect(writeLayoutToFile(VALID)).toBeNull();
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      silenceLogs();
      expect(writeLayoutToFile(overBytes())).toMatch(FRAME_REFUSAL);
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    },
    HEAVY_MS,
  );

  it(
    'the import parses it and refuses it for its bytes: nothing marked, saved file byte-identical',
    () => {
      expect(writeLayoutToFile(VALID)).toBeNull();
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      fs.writeFileSync(sourceFile(), JSON.stringify(overBytes(), null, 2));
      expect(fs.statSync(sourceFile()).size).toBeLessThanOrEqual(LAYOUT_MAX_FILE_BYTES);
      let marked = 0;
      const result = importLayoutFile(sourceFile(), () => marked++);
      expect('refused' in result && result.refused).toMatch(FRAME_REFUSAL);
      expect(marked).toBe(0);
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    },
    HEAVY_MS,
  );

  it(
    'DI saveLayout refuses it: persisted file byte-identical, persisted layout re-sent',
    () => {
      expect(writeLayoutToFile(VALID)).toBeNull();
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      const { disposition, layouts } = runDi({ type: 'saveLayout', layout: overBytes() });
      expect(disposition).toEqual({ refused: expect.stringMatching(FRAME_REFUSAL) });
      expect(layouts).toEqual([VALID]);
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    },
    HEAVY_MS,
  );

  it(
    'the file read gets past the size check, parses it, and refuses it for its bytes',
    () => {
      writeRaw(JSON.stringify(overBytes(), null, 2));
      expect(fs.statSync(layoutPath()).size).toBeLessThanOrEqual(LAYOUT_MAX_FILE_BYTES);
      const errors = silenceLogs();
      expect(readLayoutFromFile()).toBeNull();
      const lines = errorLines(errors);
      expect(lines).toHaveLength(1);
      expect(lines[0].replace('[Pixel Agents] Refusing layout file: ', '')).toMatch(FRAME_REFUSAL);
    },
    HEAVY_MS,
  );

  it(
    'the handshake never sends it to the browser (sends the bundled default)',
    () => {
      writeRaw(JSON.stringify(overBytes(), null, 2));
      silenceLogs();
      expect(runDi({ type: 'webviewReady' }).layouts).toEqual([DEFAULT_LAYOUT]);
    },
    HEAVY_MS,
  );
});

describe('F4: admission covers the MIGRATED form the consumer holds; refusal preserves the saved bytes', () => {
  const sourceFile = () => path.join(sandbox, 'chosen.json');
  const { cols, rows } = CAP;
  const cells = cols * rows;
  const color = { h: 214, s: 30, b: -100, c: -55, colorize: true };
  /** Six bytes per character in JSON: a cell carrying it costs ~770 B, so one heavy cell is the
   *  finest step there is toward the frame ceiling at the cell cap. */
  const label = heavyLabel();
  const AFTER_MIGRATION = new RegExp(`^after migration: .*over ${LAYOUT_MAX_FRAME_BYTES}$`);
  /** The reviewer's round-2 reproducer, at the cell cap: no tileColors and no pets (migration
   *  generates both); the first `labelled` cells carry the heavy label. */
  // furniture ids of six-byte characters too: with carpets capped at LAYOUT_MAX_CARPET_CELLS,
  // they keep the reproducer able to reach the frame ceiling
  const furniture = Array.from({ length: LAYOUT_MAX_FURNITURE }, (_, k) => ({
    uid: heavyLabel(k),
    type: heavyLabel(k),
    col: 0,
    row: 0,
  }));
  const reproducer = (labelled: number): Layout => ({
    version: 1,
    cols,
    rows,
    layoutRevision: 3,
    tiles: new Array(cells).fill(7),
    furniture,
    carpetTiles: Array.from({ length: cells }, (_, i) =>
      i < LAYOUT_MAX_CARPET_CELLS ? { variant: 2, color, accentColor: color, order: 1 } : null,
    ),
    areas: [
      { label, color: AREA_COLOUR },
      { label: 'a', color: AREA_COLOUR },
    ],
    areaTiles: Array.from({ length: cells }, (_, i) => (i < labelled ? label : 'a')),
  });
  /** The LARGEST raw form `make(n)` its own (legacy) check admits — found by binary search, so
   *  it sits within one heavy cell of the frame ceiling, whatever the ceiling is. */
  const atRawBoundary = (make: (n: number) => Layout): Layout =>
    make(largestAdmitted(0, cells, (n) => checkLayoutForm(make(n), { legacy: true }) === null));
  const refusedAndPreserved = (layout: Layout) => {
    expect(writeLayoutToFile(VALID)).toBeNull();
    const before = fs.readFileSync(layoutPath(), 'utf-8');
    silenceLogs();
    // the sink (every host-side save) refuses it ...
    expect(writeLayoutToFile(layout)).toMatch(AFTER_MIGRATION);
    // ... and so does the VS Code import, reporting nothing and marking nothing
    fs.writeFileSync(sourceFile(), JSON.stringify(layout));
    let marked = 0;
    const imported = importLayoutFile(sourceFile(), () => marked++);
    expect('refused' in imported && imported.refused).toMatch(AFTER_MIGRATION);
    expect(marked).toBe(0);
    // the saved layout is byte-identical
    expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
  };

  it(
    'MISSING tileColors (+ pets), the reviewer reproducer: raw form fits, migrated form does not -> refused, saved bytes preserved',
    () => {
      const layout = atRawBoundary(reproducer);
      expect(checkLayoutForm(layout, { legacy: true })).toBeNull(); // what round 2 admitted
      refusedAndPreserved(layout);
    },
    HEAVY_MS,
  );

  it(
    'WRONG-LENGTH tileColors near the ceiling: migration regenerates them -> refused, saved bytes preserved',
    () => {
      const layout = atRawBoundary((n) => ({
        ...reproducer(n),
        pets: [],
        tileColors: new Array(cells - 1).fill(null),
      }));
      expect(checkLayoutForm(layout, { legacy: true })).toBeNull();
      refusedAndPreserved(layout);
    },
    HEAVY_MS,
  );

  it(
    'OLD_VOID 8 -> 255 growth near the ceiling (no layoutRevision) is covered too -> refused, saved bytes preserved',
    () => {
      // exact tileColors and pets present, so the ONLY migration step is OLD_VOID (+2 B per tile)
      const at = (n: number): Layout => {
        const { layoutRevision: _drop, ...rest } = reproducer(n);
        return {
          ...rest,
          tiles: new Array(cells).fill(8),
          tileColors: new Array(cells).fill(null),
          pets: [],
        };
      };
      const layout = atRawBoundary(at);
      expect(checkLayoutForm(layout, { legacy: true })).toBeNull();
      refusedAndPreserved(layout);
    },
    HEAVY_MS,
  );

  it(
    'a legacy layout whose migrated form fits is still admitted (no over-refusal) — up to one heavy cell under the ceiling',
    () => {
      const n = largestAdmitted(
        0,
        cells,
        (k) => 'layout' in admitLayout(reproducer(k), { legacy: true }),
      );
      const fits = reproducer(n);
      expect('layout' in admitLayout(fits, { legacy: true })).toBe(true);
      expect(writeLayoutToFile(fits)).toBeNull();
      // the INPUT form is what is persisted, unchanged
      expect(sameJson(readLayoutFromFile(), fits)).toBe(true);
      // ... and one more heavy cell is refused, for its migrated bytes
      expect(checkLayout(reproducer(n + 1), { legacy: true })).toMatch(AFTER_MIGRATION);
    },
    HEAVY_MS,
  );
});

// The DI office's PASTE import (M3): a pasted layout is bounded and checked in the browser by the
// same intake as a file's text (webview-ui/test/pasteImport.test.ts, layoutIntake.test.ts) and reaches
// the server only as DI saveLayout. Its server end, with a malformed and an over-the-bound layout:
describe('DI paste import: the pasted layout reaches the server only as saveLayout, bounded like every other path', () => {
  it('a MALFORMED pasted layout is refused by DI saveLayout: persisted file byte-identical, persisted layout re-sent', () => {
    writeLayoutToFile(VALID);
    const before = fs.readFileSync(layoutPath(), 'utf-8');
    silenceLogs();
    const { disposition, layouts } = runDi({ type: 'saveLayout', layout: BAD['malformed pet'] });
    expect(typeof disposition).toBe('object');
    expect(layouts).toEqual([VALID]);
    expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
  });

  it(
    'an OVER-THE-BOUND pasted layout is refused by DI saveLayout for its bytes: persisted file byte-identical',
    () => {
      expect(writeLayoutToFile(VALID)).toBeNull();
      const before = fs.readFileSync(layoutPath(), 'utf-8');
      silenceLogs();
      const { disposition, layouts } = runDi({ type: 'saveLayout', layout: overBytes() });
      expect((disposition as { refused: string }).refused).toMatch(FRAME_REFUSAL);
      expect(layouts).toEqual([VALID]);
      expect(fs.readFileSync(layoutPath(), 'utf-8')).toBe(before);
    },
    HEAVY_MS,
  );
});
