import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test, vi } from 'vitest';

import {
  checkLayoutForm,
  LAYOUT_ID_MAX_LENGTH,
  LAYOUT_MAX_AREAS,
  LAYOUT_MAX_CARPET_COLOUR_VARIANTS,
  LAYOUT_MAX_CELLS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FILE_BYTES,
  LAYOUT_MAX_FRAME_BYTES,
  LAYOUT_MAX_FURNITURE,
  LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS,
  LAYOUT_MAX_PETS,
  LAYOUT_MAX_TILE_COLOUR_VARIANTS,
  LAYOUT_POSITION_BOUND,
  layoutByteSizes,
  parseLayoutText,
} from '../../core/src/layoutLimits.ts';
import {
  acceptLayout,
  applyImport,
  importFileTooLarge,
  prepareImport,
  prepareSave,
} from '../src/office/layout/layoutIntake.ts';
import { deserializeLayout } from '../src/office/layout/layoutSerializer.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf-8');
const VALID = { version: 1, cols: 2, rows: 1, tiles: [1, 1], furniture: [] };
/** Near-cap layouts take ~0.3-0.6 s per check (structure + two UTF-8 byte counts). */
const HEAVY_MS = 120_000;

// ── Shapes and fixtures AT the shared caps: every number derived from core/src/layoutLimits.ts ──
type Shape = { cols: number; rows: number };
/** The widest grid with EXACTLY LAYOUT_MAX_CELLS cells, both axes within LAYOUT_MAX_DIM. */
const CAP: Shape = (() => {
  for (let cols = LAYOUT_MAX_DIM; cols >= 1; cols--) {
    const rows = LAYOUT_MAX_CELLS / cols;
    if (Number.isInteger(rows) && rows <= LAYOUT_MAX_DIM) return { cols, rows };
  }
  throw new Error('no grid within LAYOUT_MAX_DIM has exactly LAYOUT_MAX_CELLS cells');
})();
const WIDEST: Shape = { cols: LAYOUT_MAX_DIM, rows: Math.floor(LAYOUT_MAX_CELLS / LAYOUT_MAX_DIM) };
const TALLEST: Shape = { cols: WIDEST.rows, rows: WIDEST.cols };
const JUST_OVER_CELLS: Shape = { cols: WIDEST.cols, rows: WIDEST.rows + 1 };
const DIM_SQUARED: Shape = { cols: LAYOUT_MAX_DIM, rows: LAYOUT_MAX_DIM };
/** The connected metropolis grid: the shipped forest as one quadrant (twice it on each axis). */
const FOREST = JSON.parse(read('packaging/di/worlds/vast-forest.json')) as Shape;
const METRO: Shape = { cols: FOREST.cols * 2, rows: FOREST.rows * 2 };

const tilesOnly = ({ cols, rows }: Shape) => ({
  version: 1,
  cols,
  rows,
  tiles: new Array(cols * rows).fill(1),
  furniture: [],
});
/* eslint-disable pixel-agents/no-inline-colors -- area colour as layout test data */
const AREA_COLOUR = '#336699';
/* eslint-enable pixel-agents/no-inline-colors */
/** The longest JSON number spelling the structural check admits (finite, within the bound). */
const LONG = -2.2250738585072014e-308;
const LONG_COLOUR = { h: LONG, s: LONG, b: LONG, c: LONG, colorize: false };
/** A LAYOUT_ID_MAX_LENGTH-character label JSON escapes to six bytes per character (\u0001). */
const HEAVY_LABEL = '\u0001'.repeat(LAYOUT_ID_MAX_LENGTH);
/** STRUCTURALLY valid at the cell cap and already migrated, but its save frame is over
 *  LAYOUT_MAX_FRAME_BYTES (its pretty file stays under the file ceiling). */
const overBytes = () => {
  const cells = CAP.cols * CAP.rows;
  return {
    version: 1,
    cols: CAP.cols,
    rows: CAP.rows,
    tiles: new Array(cells).fill(1),
    tileColors: new Array(cells).fill(LONG_COLOUR),
    furniture: [],
    pets: [],
    areas: [{ label: HEAVY_LABEL, color: AREA_COLOUR }],
    areaTiles: new Array(cells).fill(HEAVY_LABEL),
  };
};
const FRAME_REFUSAL = new RegExp(
  `^layout save frame is \\d+ bytes \\(UTF-8\\), over ${LAYOUT_MAX_FRAME_BYTES}$`,
);
const furnitureItems = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ uid: `f${i}`, type: 'DESK_FRONT', col: 0, row: 0 }));
const petList = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, petType: 0 }));
const areaList = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ label: `A${i}`, color: AREA_COLOUR }));
/** The k-th DISTINCT editor colour (k = 0: h -180, s -100). */
const variantColour = (k: number) => ({
  h: -180 + (k % 81),
  s: -100 + Math.floor(k / 81),
  b: -100,
  c: -100,
  colorize: true,
});
/** n cells (64 wide), each a DIFFERENT tile / carpet colour variant; n items, each a different
 *  furniture (type, colour) variant. */
const variantGrid = (n: number) => tilesOnly({ cols: 64, rows: Math.ceil(n / 64) });
const tileVariants = (n: number) => {
  const g = variantGrid(n);
  return { ...g, tileColors: g.tiles.map((_, i) => (i < n ? variantColour(i) : null)) };
};
const carpetVariants = (n: number) => {
  const g = variantGrid(n);
  return {
    ...g,
    carpetTiles: g.tiles.map((_, i) => (i < n ? { variant: 0, color: variantColour(i) } : null)),
  };
};
const furnitureVariants = (n: number) => ({
  ...VALID,
  furniture: Array.from({ length: n }, (_, i) => ({
    uid: `v${i}`,
    type: 'DESK_FRONT',
    col: 0,
    row: 0,
    color: variantColour(i),
  })),
});

const BAD: Record<string, unknown> = {
  'oversized dims': {
    ...VALID,
    cols: LAYOUT_MAX_DIM + 1,
    tiles: new Array(LAYOUT_MAX_DIM + 1).fill(1),
  },
  'oversized dims (rows)': {
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
  'mismatched parallel array (tiles)': { ...VALID, tiles: [1] },
  'mismatched parallel array (carpetTiles)': { ...VALID, carpetTiles: [null] },
  'mismatched parallel array (areaTiles)': { ...VALID, areas: [], areaTiles: [null] },
  'unknown top-level field': { ...VALID, script: 'x' },
  'unknown furniture field': {
    ...VALID,
    furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: 0, onload: 'x' }],
  },
  'malformed colour': { ...VALID, tileColors: [null, { h: 'red', s: 0, b: 0, c: 0 }] },
  'malformed pet': { ...VALID, pets: [{ id: '', petType: 0 }] },
  'malformed area': { ...VALID, areas: [{ label: 'A', color: 'red' }] },
  'version 2': { ...VALID, version: 2 },
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
/** Each CAP class is refused for its OWN cap, not an incidental flaw. */
const CAP_REASONS: Record<string, string> = {
  'oversized dims': `layout cols/rows outside 1..${LAYOUT_MAX_DIM}`,
  'oversized dims (rows)': `layout cols/rows outside 1..${LAYOUT_MAX_DIM}`,
  'over the total-cell cap (each axis within LAYOUT_MAX_DIM)': `layout cols x rows is ${JUST_OVER_CELLS.cols * JUST_OVER_CELLS.rows} cells, over ${LAYOUT_MAX_CELLS}`,
  'DIM x DIM (both axes AT LAYOUT_MAX_DIM, cells over the cap)': `layout cols x rows is ${LAYOUT_MAX_DIM * LAYOUT_MAX_DIM} cells, over ${LAYOUT_MAX_CELLS}`,
  'furniture over LAYOUT_MAX_FURNITURE': 'layout furniture missing or too long',
  'pets over LAYOUT_MAX_PETS': 'layout pets malformed',
  'areas over LAYOUT_MAX_AREAS': 'layout areas malformed',
  'tile colour variants over LAYOUT_MAX_TILE_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_TILE_COLOUR_VARIANTS} distinct tile colour variants`,
  'carpet colour variants over LAYOUT_MAX_CARPET_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_CARPET_COLOUR_VARIANTS} distinct carpet colour variants`,
  'furniture colour variants over LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS': `layout has over ${LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS} distinct furniture colour variants`,
  'position over LAYOUT_POSITION_BOUND': 'layout furniture item malformed',
  ...HOME_REASONS,
};
/** Valid homes, up to the grid's own bounds: admitted, and KEPT, at every entry path. */
const HOMED: Record<string, () => { home: unknown }> = {
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
/** Exactly AT each cap: admitted at every entry path (no off-by-one over-refusal). */
const AT_CAP: Record<string, () => unknown> = {
  [`exactly the cell cap (${CAP.cols} x ${CAP.rows}), tiles only`]: () => tilesOnly(CAP),
  [`the widest grid within the cell cap (${WIDEST.cols} x ${WIDEST.rows})`]: () =>
    tilesOnly(WIDEST),
  [`the tallest grid within the cell cap (${TALLEST.cols} x ${TALLEST.rows})`]: () =>
    tilesOnly(TALLEST),
  [`the connected metropolis grid (${METRO.cols} x ${METRO.rows})`]: () => tilesOnly(METRO),
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
  ...HOMED,
};
const LEGACY: Record<string, unknown> = {
  'old VOID value 8': { version: 1, cols: 2, rows: 1, tiles: [8, 1], furniture: [] },
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
  'tileColors of the wrong length': {
    version: 1,
    cols: 2,
    rows: 1,
    tiles: [1, 1],
    tileColors: [null],
    furniture: [],
  },
};
const SHIPPED = [
  'webview-ui/public/assets/default-layout-1.json',
  'packaging/di/worlds/expanded-office.json',
  'packaging/di/worlds/vast-forest.json',
];

test(
  'the over-bytes fixture is within every structural cap, over the frame ceiling, its file under the file ceiling',
  () => {
    const layout = overBytes();
    const { frame, file } = layoutByteSizes(layout);
    assert.ok(frame > LAYOUT_MAX_FRAME_BYTES, `frame ${frame}`);
    assert.ok(file <= LAYOUT_MAX_FILE_BYTES, `file ${file}`); // so Import gets past File.size
    // bytes are the LAST check: this reason means every structural check passed
    assert.match(checkLayoutForm(layout, { legacy: true }) ?? '', FRAME_REFUSAL);
  },
  HEAVY_MS,
);

// ── layoutLoaded (acceptLayout is the handler's gate) ──
for (const [name, layout] of Object.entries(BAD)) {
  test(`layoutLoaded refuses: ${name}`, () => {
    const result = acceptLayout(layout);
    assert.ok('refused' in result);
    if (name in CAP_REASONS) assert.equal(result.refused, CAP_REASONS[name]);
  });
}
test(
  'layoutLoaded refuses a structurally valid layout over the BYTE ceiling',
  () => {
    const result = acceptLayout(overBytes());
    assert.ok('refused' in result);
    assert.match(result.refused, FRAME_REFUSAL);
  },
  HEAVY_MS,
);
for (const [name, make] of Object.entries(AT_CAP)) {
  test(`layoutLoaded accepts: ${name}`, () => {
    const layout = make() as { cols: number; rows: number };
    const result = acceptLayout(layout);
    assert.ok('layout' in result);
    assert.equal(result.layout.cols * result.layout.rows, layout.cols * layout.rows);
    assert.equal(result.layout.tiles.length, layout.cols * layout.rows);
  });
}
test('layoutLoaded accepts legacy layouts, migrating them', () => {
  const voidResult = acceptLayout(LEGACY['old VOID value 8']);
  assert.ok('layout' in voidResult);
  assert.equal(voidResult.layout.tiles[0], TileType.VOID); // OLD_VOID 8 -> 255
  const legacyTypes = acceptLayout(LEGACY['legacy furniture type strings']);
  assert.ok('layout' in legacyTypes);
  assert.deepEqual(
    legacyTypes.layout.furniture.map((f) => f.type),
    ['DESK_FRONT'], // 'desk' migrated, 'cooler' (no equivalent) removed
  );
  for (const [name, layout] of Object.entries(LEGACY))
    assert.ok('layout' in acceptLayout(layout), name);
});
test('layoutLoaded accepts every shipped layout (20x11 default, 60x27 preset, forest world)', () => {
  for (const rel of SHIPPED) assert.ok('layout' in acceptLayout(JSON.parse(read(rel))), rel);
});

// ── deserializeLayout ──
for (const [name, layout] of Object.entries(BAD)) {
  test(`deserializeLayout refuses: ${name}`, () => {
    assert.equal(deserializeLayout(JSON.stringify(layout)), null);
  });
}
test(
  'deserializeLayout refuses a structurally valid layout over the BYTE ceiling',
  () => {
    assert.equal(deserializeLayout(JSON.stringify(overBytes())), null);
  },
  HEAVY_MS,
);
for (const [name, make] of Object.entries(AT_CAP)) {
  test(`deserializeLayout accepts: ${name}`, () => {
    const layout = make() as { cols: number; rows: number };
    const result = deserializeLayout(JSON.stringify(layout));
    assert.ok(result);
    assert.equal(result.tiles.length, layout.cols * layout.rows);
  });
}
test('deserializeLayout accepts legacy and shipped layouts', () => {
  for (const layout of Object.values(LEGACY)) assert.ok(deserializeLayout(JSON.stringify(layout)));
  for (const rel of SHIPPED) assert.ok(deserializeLayout(read(rel)), rel);
});

// ── Import Layout ──
test('import: an oversized FILE is refused by File.size, before it is read', () => {
  assert.equal(importFileTooLarge(LAYOUT_MAX_FILE_BYTES), false);
  assert.equal(importFileTooLarge(LAYOUT_MAX_FILE_BYTES + 1), true);
});
for (const [name, layout] of Object.entries(BAD)) {
  test(`import refuses: ${name}`, () => {
    const result = prepareImport(JSON.stringify(layout, null, 2));
    assert.ok('refused' in result);
    if (name in CAP_REASONS) assert.equal(result.refused, CAP_REASONS[name]);
  });
}
test('import refuses text that is not JSON', () => {
  assert.ok('refused' in prepareImport('{ nope'));
});
test(
  'import refuses a structurally valid file over the BYTE ceiling (within File.size, refused by content)',
  () => {
    const text = JSON.stringify(overBytes(), null, 2);
    assert.equal(importFileTooLarge(new TextEncoder().encode(text).length), false);
    const result = prepareImport(text);
    assert.ok('refused' in result);
    assert.match(result.refused, FRAME_REFUSAL);
  },
  HEAVY_MS,
);
for (const [name, make] of Object.entries(AT_CAP)) {
  test(`import accepts: ${name}`, () => {
    const text = JSON.stringify(make(), null, 2);
    assert.equal(importFileTooLarge(text.length), false);
    assert.ok('layout' in prepareImport(text));
  });
}

function recordingEffects() {
  const calls: string[] = [];
  return {
    calls,
    effects: {
      rebuild: () => calls.push('rebuild'),
      setLastSaved: () => calls.push('setLastSaved'),
      send: () => calls.push('send'),
      markClean: () => calls.push('markClean'),
      refuse: () => calls.push('refuse'),
    },
  };
}
test(
  'a REFUSED import changes nothing: not rendered, not sent, NOT marked clean',
  () => {
    for (const layout of [...Object.values(BAD), overBytes()]) {
      const { calls, effects } = recordingEffects();
      assert.equal(applyImport(JSON.stringify(layout), effects), false);
      assert.deepEqual(calls, ['refuse']);
    }
  },
  HEAVY_MS,
);
test('an accepted import renders, records, sends and only then marks clean', () => {
  const { calls, effects } = recordingEffects();
  assert.equal(applyImport(read('packaging/di/worlds/vast-forest.json'), effects), true);
  assert.deepEqual(calls, ['rebuild', 'setLastSaved', 'send', 'markClean']);
});
test('an accepted import of a cell-cap layout is applied in full', () => {
  const { calls, effects } = recordingEffects();
  assert.equal(applyImport(JSON.stringify(tilesOnly(CAP)), effects), true);
  assert.deepEqual(calls, ['rebuild', 'setLastSaved', 'send', 'markClean']);
});

// ── Editor save (review F2): never sent, never recorded clean, unless admission passes ──
test(
  'an editor save is gated by the same strict check, bytes included (UTF-8)',
  () => {
    const forest = JSON.parse(read('packaging/di/worlds/vast-forest.json'));
    assert.ok('layout' in prepareSave(forest));
    assert.ok('layout' in prepareSave(tilesOnly(CAP) as never)); // the cell cap itself is saveable
    const save = prepareSave(overBytes() as never);
    assert.ok('refused' in save);
    assert.match(save.refused, FRAME_REFUSAL);
    const cells = prepareSave(tilesOnly(JUST_OVER_CELLS) as never);
    assert.ok('refused' in cells);
    assert.equal(
      cells.refused,
      CAP_REASONS['over the total-cell cap (each axis within LAYOUT_MAX_DIM)'],
    );
  },
  HEAVY_MS,
);

// ── The explicit HOME: kept wherever it is admitted, refused for itself wherever it is not ──
for (const [name, make] of Object.entries(HOMED)) {
  test(`home KEPT by layoutLoaded, deserializeLayout, import and the editor save: ${name}`, () => {
    const layout = make();
    const loaded = acceptLayout(layout);
    assert.ok('layout' in loaded);
    assert.deepEqual(loaded.layout.home, layout.home);
    assert.deepEqual(deserializeLayout(JSON.stringify(layout))?.home, layout.home);
    const imported = prepareImport(JSON.stringify(layout, null, 2));
    assert.ok('layout' in imported);
    assert.deepEqual(imported.layout.home, layout.home);
    const saved = prepareSave(loaded.layout);
    assert.ok('layout' in saved);
    assert.deepEqual(saved.layout.home, layout.home);
  });
}
test('an editor save with a malformed home is refused for the home', () => {
  for (const [name, reason] of Object.entries(HOME_REASONS)) {
    const save = prepareSave(BAD[name] as never);
    assert.ok('refused' in save, name);
    assert.equal(save.refused, reason, name);
  }
});

// ── F3 (review round 1): the INPUT-BYTE gate runs BEFORE JSON.parse at every text entry path ──
test(
  'oversized layout TEXT (a tiny layout behind whitespace) is refused BEFORE parsing — deserializer, Import, shared file parse',
  () => {
    const tiny = JSON.stringify(VALID);
    // UTF-8 bytes = LAYOUT_MAX_FILE_BYTES + 1: one byte over, all of it whitespace admission never sees
    const over = ' '.repeat(LAYOUT_MAX_FILE_BYTES + 1 - tiny.length) + tiny;
    const parse = vi.spyOn(JSON, 'parse');
    try {
      assert.equal(deserializeLayout(over), null);
      const imported = prepareImport(over);
      assert.ok('refused' in imported && /over \d+ bytes \(UTF-8\)/.test(imported.refused));
      const parsed = parseLayoutText(over);
      assert.ok('refused' in parsed);
      assert.equal(parse.mock.calls.length, 0, 'JSON.parse never ran on the oversized text');
    } finally {
      parse.mockRestore();
    }
    // exactly at the ceiling: parsed and admitted (no over-refusal)
    const atCap = ' '.repeat(LAYOUT_MAX_FILE_BYTES - tiny.length) + tiny;
    assert.notEqual(deserializeLayout(atCap), null);
    assert.ok('layout' in prepareImport(atCap));
    assert.ok('layout' in parseLayoutText(atCap));
  },
  HEAVY_MS,
);

test(
  'multibyte text under the ceiling in code units but over it in UTF-8 bytes is COUNTED and refused before parsing',
  () => {
    // a three-byte character inside a string value: fewer code units than the ceiling, more bytes
    const n = Math.floor(LAYOUT_MAX_FILE_BYTES / 3) + 1;
    const text = `{"version":1,"cols":1,"rows":1,"tiles":[1],"furniture":[],"note":"${'界'.repeat(n)}"}`;
    assert.ok(text.length <= LAYOUT_MAX_FILE_BYTES, 'under the ceiling by code units');
    const parse = vi.spyOn(JSON, 'parse');
    try {
      assert.equal(deserializeLayout(text), null);
      assert.ok('refused' in prepareImport(text));
      assert.ok('refused' in parseLayoutText(text));
      assert.equal(parse.mock.calls.length, 0);
    } finally {
      parse.mockRestore();
    }
  },
  HEAVY_MS,
);
