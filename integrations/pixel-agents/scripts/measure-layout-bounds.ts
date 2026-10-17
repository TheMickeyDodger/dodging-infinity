/**
 * MEASURES (does not assume) the layout byte ceilings and the near-cap cost of every server-side
 * layout stage, on layouts CONSTRUCTED at the structural caps of core/src/layoutLimits.ts.
 *
 *   npx tsx scripts/measure-layout-bounds.ts sizes                 # byte sizes of the shapes below
 *   npx tsx scripts/measure-layout-bounds.ts write <dir>           # near-cap + pathological files
 *   npx tsx scripts/measure-layout-bounds.ts stage <name> <dir>    # ONE stage, in a fresh process
 *   npx tsx scripts/measure-layout-bounds.ts all <dir>             # every stage, each in its own
 *                                                                  # child; prints the JSON report
 *
 * Shapes (all at LAYOUT_MAX_CELLS, the largest ADMITTED grid — not the shipped world's):
 *   maximalLegitimate  — every cell coloured (editor HSBC extremes, colorize), LAYOUT_MAX_CARPET_CELLS
 *                        of them carpeted with colour + accent + order (the carpeted-cell cap),
 *                        every cell in an area whose label is LAYOUT_ID_MAX_LENGTH ASCII
 *                        characters; LAYOUT_MAX_FURNITURE coloured items with uid AND type at the
 *                        character cap and positions at the position bound; LAYOUT_MAX_PETS pets
 *                        and LAYOUT_MAX_AREAS areas with ids/labels at the cap; EXACTLY AT the three
 *                        distinct colour-variant caps (1,024 tile, 256 carpet, 1,024 furniture).
 *
 * WHAT THESE SHAPES DO NOT COVER: they are BYTE and SERVER-ADMISSION probes. Their tiles are VOID
 * (255), their furniture types are invented ids and they sit off-world, so a browser draws none of
 * it. The BROWSER render cost is measured by the separate `render` workload below (known types,
 * in view, real floors and carpets, at the caps), never by these.
 *   structuralWorst    — the same, but every number at its LONGEST JSON spelling that the
 *                        structural check still admits, and every label/id made of characters
 *                        JSON escapes to six bytes. Structurally valid; must be REFUSED by bytes.
 *   nearCap            — maximalLegitimate grown (long-number colours on a fraction of cells)
 *                        until it sits just under BOTH byte ceilings: the largest layout
 *                        admission accepts, which every later stage must then handle.
 * Peak memory per stage is the child's own maxRSS (process.resourceUsage), so stages never
 * share a heap.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

import {
  admitLayout,
  checkLayoutForm,
  LAYOUT_ID_MAX_LENGTH,
  LAYOUT_MAX_AREAS,
  LAYOUT_MAX_CARPET_CELLS,
  LAYOUT_MAX_CELLS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FILE_BYTES,
  LAYOUT_MAX_FRAME_BYTES,
  LAYOUT_MAX_CARPET_COLOUR_VARIANTS,
  LAYOUT_MAX_FURNITURE,
  LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS,
  LAYOUT_MAX_PETS,
  LAYOUT_MAX_TILE_COLOUR_VARIANTS,
  LAYOUT_POSITION_BOUND,
  layoutByteSizes,
  parseLayoutText,
} from '../core/src/layoutLimits.js';
import { migrateLayout } from '../core/src/layoutMigration.js';

type Colour = { h: number; s: number; b: number; c: number; colorize?: boolean };

/** Editor output at its widest spelling (HSBC sliders: h -180..180, s/b/c -100..100). */
const EDITOR_COLOUR: Colour = { h: -180, s: -100, b: -100, c: -100, colorize: true };
/** The longest JSON number spelling isLayoutColor admits (finite, |v| <= 1000): 24 chars. */
const LONG = -2.2250738585072014e-308;
const LONG_COLOUR: Colour = { h: LONG, s: LONG, b: LONG, c: LONG, colorize: false };
/**
 * The k-th DISTINCT editor colour, as wide as the editor spells it: k = 0 IS EDITOR_COLOUR; h
 * walks -180..-100 (four characters), then s steps up from -100. Used to put each byte shape
 * exactly AT the distinct colour-variant caps (LAYOUT_MAX_*_COLOUR_VARIANTS) — the most colorize
 * cache entries an admitted layout can create — rather than at one or two colours.
 */
const variantColour = (k: number): Colour => ({
  h: -180 + (k % 81),
  s: -100 + Math.floor(k / 81),
  b: -100,
  c: -100,
  colorize: true,
});

const ascii = (prefix: string, n: number) =>
  (prefix + 'x'.repeat(LAYOUT_ID_MAX_LENGTH)).slice(0, n);
/** A string of `n` UTF-16 units that JSON.stringify escapes to 6 bytes each (\u0001). */
const escaped = (seed: number, n: number) =>
  String.fromCharCode(1 + (seed % 31), 1 + (Math.floor(seed / 31) % 31)) + '\u0001'.repeat(n - 2);

/** The grid shape used at the cell cap (cols x rows == LAYOUT_MAX_CELLS exactly). */
export const CAP_COLS = 320;
export const CAP_ROWS = LAYOUT_MAX_CELLS / CAP_COLS;

interface Options {
  cols: number;
  rows: number;
  colourFor: (i: number) => Colour;
  /** Carpet colour + accent per cell (default colourFor). */
  carpetColourFor?: (i: number) => Colour;
  label: (k: number) => string;
  id: (k: number) => string;
  revision: number;
  /** Which area a cell is in (default: round-robin over every area). */
  areaOf?: (i: number) => number;
}

function build(o: Options): Record<string, unknown> {
  const cells = o.cols * o.rows;
  const areas = Array.from({ length: LAYOUT_MAX_AREAS }, (_, k) => ({
    label: o.label(k),
    color: '#a0b0c0',
  }));
  const tiles = new Array<number>(cells).fill(255);
  const tileColors = Array.from({ length: cells }, (_, i) => o.colourFor(i));
  const carpetColourFor = o.carpetColourFor ?? o.colourFor;
  // carpeted on exactly LAYOUT_MAX_CARPET_CELLS cells (the carpeted-cell cap)
  const carpetTiles = Array.from({ length: cells }, (_, i) =>
    i < LAYOUT_MAX_CARPET_CELLS
      ? {
          variant: 255,
          color: carpetColourFor(i),
          accentColor: carpetColourFor(i),
          order: -1_000_000,
        }
      : null,
  );
  const areaOf = o.areaOf ?? ((i: number) => i % LAYOUT_MAX_AREAS);
  const areaTiles = Array.from({ length: cells }, (_, i) => areas[areaOf(i)].label);
  // (type, colour) pairs are a counted variant: LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS types at the
  // id cap, each with ONE colour — every item still has a type at the character cap
  const furnitureVariant = (k: number) => k % LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS;
  const furniture = Array.from({ length: LAYOUT_MAX_FURNITURE }, (_, k) => ({
    uid: o.id(k),
    type: o.id(furnitureVariant(k) + 1_000_000),
    col: -LAYOUT_POSITION_BOUND,
    row: -LAYOUT_POSITION_BOUND,
    color: o.colourFor(furnitureVariant(k)),
  }));
  const pets = Array.from({ length: LAYOUT_MAX_PETS }, (_, k) => ({ id: o.id(k), petType: 255 }));
  return {
    version: 1,
    cols: o.cols,
    rows: o.rows,
    layoutRevision: o.revision,
    tiles,
    tileColors,
    furniture,
    pets,
    carpetTiles,
    areas,
    areaTiles,
  };
}

const legitId = (k: number) => ascii(`id-${k}-`, LAYOUT_ID_MAX_LENGTH);
const legitLabel = (k: number) => ascii(`district-${k}-`, LAYOUT_ID_MAX_LENGTH);

export function maximalLegitimate(cols = CAP_COLS, rows = CAP_ROWS) {
  return build({
    cols,
    rows,
    // exactly AT the variant caps: LAYOUT_MAX_TILE_COLOUR_VARIANTS distinct tile colours (and so
    // LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS furniture pairs), LAYOUT_MAX_CARPET_COLOUR_VARIANTS
    // carpet triples; every other cell EDITOR_COLOUR (= variant 0)
    colourFor: (i) => variantColour(i < LAYOUT_MAX_TILE_COLOUR_VARIANTS ? i : 0),
    carpetColourFor: (i) => variantColour(i < LAYOUT_MAX_CARPET_COLOUR_VARIANTS ? i : 0),
    label: legitLabel,
    id: legitId,
    revision: 1_000_000,
  });
}

export function structuralWorst(cols = CAP_COLS, rows = CAP_ROWS) {
  return build({
    cols,
    rows,
    colourFor: () => LONG_COLOUR,
    label: (k) => escaped(k, LAYOUT_ID_MAX_LENGTH),
    id: (k) => escaped(k, LAYOUT_ID_MAX_LENGTH),
    revision: 1_000_000,
  });
}

/**
 * maximalLegitimate grown in two steps: long-number colours on the first `colourFraction` of
 * cells, then the first `labelFraction` of cells moved into areas 32..63, whose labels are made of
 * characters JSON escapes to six bytes. Both only add bytes; the structure stays at the caps.
 */
const HALF = LAYOUT_MAX_AREAS / 2;
function grown(colourFraction: number, labelFraction: number) {
  const colourCut = Math.floor(LAYOUT_MAX_CELLS * colourFraction);
  const labelCut = Math.floor(LAYOUT_MAX_CELLS * labelFraction);
  return build({
    cols: CAP_COLS,
    rows: CAP_ROWS,
    colourFor: (i) => (i < colourCut ? LONG_COLOUR : EDITOR_COLOUR),
    label: (k) => (k < HALF ? legitLabel(k) : escaped(k, LAYOUT_ID_MAX_LENGTH)),
    areaOf: (i) => (i < labelCut ? HALF + (i % HALF) : i % HALF),
    id: legitId,
    revision: 1_000_000,
  });
}

const fits = (layout: Record<string, unknown>) => {
  const { frame, file } = layoutByteSizes(layout);
  return frame <= LAYOUT_MAX_FRAME_BYTES && file <= LAYOUT_MAX_FILE_BYTES;
};

/** The largest admitted layout: binary search on the colour, then the label, fraction. */
export function nearCap(): {
  layout: Record<string, unknown>;
  colourFraction: number;
  labelFraction: number;
} {
  const search = (make: (f: number) => Record<string, unknown>) => {
    let lo = 0;
    let hi = 1;
    if (fits(make(1))) return 1;
    for (let step = 0; step < 18; step++) {
      const mid = (lo + hi) / 2;
      if (fits(make(mid))) lo = mid;
      else hi = mid;
    }
    return lo;
  };
  const colourFraction = search((f) => grown(f, 0));
  const labelFraction = colourFraction < 1 ? 0 : search((f) => grown(1, f));
  return { layout: grown(colourFraction, labelFraction), colourFraction, labelFraction };
}

const mb = (kb: number) => Math.round((kb / 1024) * 10) / 10;
const now = () => Number(process.hrtime.bigint()) / 1e6;

function runStage(name: string, dir: string): Record<string, unknown> {
  const nearFile = path.join(dir, 'near-cap.layout.json');
  const worstFile = path.join(dir, 'structural-worst.layout.json');
  const t0 = now();
  let detail: Record<string, unknown> = {};
  switch (name) {
    case 'baseline':
      break;
    case 'read-parse': {
      // the server's bounded file read: stat, read, parse, LEGACY admission (parseLayoutText)
      const size = fs.statSync(nearFile).size;
      const result = parseLayoutText(fs.readFileSync(nearFile, 'utf-8'));
      detail = { fileBytes: size, admitted: !('refused' in result) };
      break;
    }
    case 'json-parse-only': {
      const text = fs.readFileSync(nearFile, 'utf-8');
      const t1 = now();
      JSON.parse(text);
      detail = { parseMs: Math.round(now() - t1) };
      break;
    }
    case 'migrate': {
      const layout = JSON.parse(fs.readFileSync(nearFile, 'utf-8'));
      const t1 = now();
      const { changed } = migrateLayout(layout);
      detail = { migrateMs: Math.round((now() - t1) * 10) / 10, changed };
      break;
    }
    case 'admit': {
      const layout = JSON.parse(fs.readFileSync(nearFile, 'utf-8'));
      const t1 = now();
      const result = admitLayout(layout, { legacy: true });
      detail = { admitMs: Math.round(now() - t1), admitted: !('refused' in result) };
      break;
    }
    case 'save-frame': {
      // the client's saveLayout frame (compact) and the server's pretty write
      const layout = JSON.parse(fs.readFileSync(nearFile, 'utf-8'));
      const t1 = now();
      const frame = JSON.stringify({ type: 'saveLayout', layout });
      const t2 = now();
      const pretty = JSON.stringify(layout, null, 2);
      const t3 = now();
      detail = {
        frameBytes: Buffer.byteLength(frame),
        frameMs: Math.round(t2 - t1),
        fileBytes: Buffer.byteLength(pretty),
        prettyMs: Math.round(t3 - t2),
      };
      break;
    }
    case 'refuse-worst': {
      // a structurally valid but over-byte layout: refused, and how much refusing it costs
      const layout = JSON.parse(fs.readFileSync(worstFile, 'utf-8'));
      const t1 = now();
      const form = checkLayoutForm(layout, { legacy: true });
      detail = { refusedMs: Math.round(now() - t1), refused: form };
      break;
    }
    default:
      throw new Error(`unknown stage ${name}`);
  }
  return {
    stage: name,
    wallMs: Math.round(now() - t0),
    peakRssMb: mb(process.resourceUsage().maxRSS),
    ...detail,
  };
}

const STAGES = [
  'baseline',
  'json-parse-only',
  'read-parse',
  'migrate',
  'admit',
  'save-frame',
  'refuse-worst',
];

function sizesReport() {
  const shapes: Record<string, Record<string, unknown>> = {
    [`maximalLegitimate ${CAP_COLS}x${CAP_ROWS}`]: maximalLegitimate(),
    [`maximalLegitimate ${LAYOUT_MAX_DIM}x${Math.floor(LAYOUT_MAX_CELLS / LAYOUT_MAX_DIM)}`]:
      maximalLegitimate(LAYOUT_MAX_DIM, Math.floor(LAYOUT_MAX_CELLS / LAYOUT_MAX_DIM)),
    'maximalLegitimate 352x224 (the shipped grid)': maximalLegitimate(352, 224),
    [`structuralWorst ${CAP_COLS}x${CAP_ROWS}`]: structuralWorst(),
  };
  const out: Record<string, unknown> = {};
  for (const [name, layout] of Object.entries(shapes)) {
    const sizes = layoutByteSizes(layout);
    out[name] = { ...sizes, structure: checkLayoutForm(layout) ?? 'admitted' };
  }
  return out;
}

/**
 * THE ACTUAL-RENDER WORKLOAD (Amendment 18) — unlike the byte shapes above (unknown 128-char
 * types parked off-world, which the renderer never draws), this layout DRAWS: LAYOUT_MAX_CELLS
 * cells (320 x 256) of real floor patterns, every cell a DISTINCT floor colour and carpeted with
 * distinct colours; LAYOUT_MAX_FURNITURE (16,384) items of KNOWN renderable catalogue types placed
 * inside the grid, every one a DISTINCT colour (the colorize caches' worst case: one entry per
 * item — the most a single admitted layout can create); LAYOUT_MAX_PETS pets of the two real pet
 * types. It must itself be ADMITTED (it is measured against both byte ceilings).
 */
export function renderWorkload(types: string[], uncapped = false): Record<string, unknown> {
  const cols = CAP_COLS;
  const rows = CAP_ROWS;
  const cells = cols * rows;
  const floors = [1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 18, 19, 20, 21, 25, 26];
  const tiles = Array.from({ length: cells }, (_, i) => floors[i % floors.length]);
  const colour = (i: number) => ({
    h: i % 360,
    s: (Math.floor(i / 360) % 200) - 100,
    b: (Math.floor(i / 72_000) % 200) - 100,
    c: 0,
  });
  // distinct colour variants exactly AT the caps (LAYOUT_MAX_*_COLOUR_VARIANTS), or `uncapped`
  // (one per cell / item: the shape that was measured to stall, now refused by admission)
  const tileVariants = uncapped ? cells : LAYOUT_MAX_TILE_COLOUR_VARIANTS;
  const carpetVariants = uncapped ? cells : LAYOUT_MAX_CARPET_COLOUR_VARIANTS;
  const furnitureVariants = uncapped ? LAYOUT_MAX_FURNITURE : LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS;
  // a tile variant is (floor value, colour): cycle both together so exactly tileVariants exist
  for (let i = 0; i < cells; i++) tiles[i] = floors[(i % tileVariants) % floors.length];
  const tileColors = Array.from({ length: cells }, (_, i) => colour(i % tileVariants));
  const carpetTiles = Array.from({ length: cells }, (_, i) => ({
    variant: (i % carpetVariants) % 3,
    color: colour((i % carpetVariants) + 1),
    accentColor: colour((i % carpetVariants) + 2),
    order: 1,
  }));
  const furniture = Array.from({ length: LAYOUT_MAX_FURNITURE }, (_, k) => {
    const v = k % furnitureVariants;
    return {
      uid: `r-${k}`,
      type: types[(uncapped ? k : v) % types.length],
      col: (k * 5) % cols,
      row: Math.floor((k * 5) / cols) % rows,
      color: {
        h: v % 360,
        s: (Math.floor(v / 360) % 100) - 50,
        b: (Math.floor(v / 36_000) % 40) - 20,
        c: 0,
      },
    };
  });
  const pets = Array.from({ length: LAYOUT_MAX_PETS }, (_, k) => ({
    id: `pet-${k}`,
    petType: k % 2,
  }));
  return {
    version: 1,
    cols,
    rows,
    layoutRevision: 1,
    tiles,
    tileColors,
    furniture,
    pets,
    carpetTiles,
  };
}

const [cmd, a1, a2] = process.argv.slice(2);
if (cmd === 'sizes') {
  console.log(JSON.stringify(sizesReport(), null, 2));
} else if (cmd === 'write') {
  fs.mkdirSync(a1, { recursive: true });
  const near = nearCap();
  fs.writeFileSync(path.join(a1, 'near-cap.layout.json'), JSON.stringify(near.layout, null, 2));
  fs.writeFileSync(
    path.join(a1, 'structural-worst.layout.json'),
    JSON.stringify(structuralWorst(), null, 2),
  );
  console.log(
    JSON.stringify({
      colourFraction: near.colourFraction,
      labelFraction: near.labelFraction,
      structure: checkLayoutForm(near.layout, { legacy: true }) ?? 'admitted',
      ...layoutByteSizes(near.layout),
    }),
  );
} else if (cmd === 'render') {
  // a1: output file; the known renderable types come from the bundled manifests (decor, 1-2 tiles)
  const dir = path.join(__dirname, '../webview-ui/public/assets/furniture');
  const types: string[] = [];
  for (const id of fs.readdirSync(dir)) {
    const m = JSON.parse(fs.readFileSync(path.join(dir, id, 'manifest.json'), 'utf-8'));
    if (m.type === 'asset' && m.category === 'decor' && m.footprintW <= 2 && m.footprintH <= 2)
      types.push(m.id);
  }
  types.sort();
  const layout = renderWorkload(types, a2 === 'uncapped');
  fs.writeFileSync(a1, JSON.stringify(layout, null, 2));
  console.log(
    JSON.stringify({
      types: types.length,
      furnitureVariants: new Set(
        (layout.furniture as Array<{ type: string; color: unknown }>).map(
          (f) => f.type + JSON.stringify(f.color),
        ),
      ).size,
      tileVariants: new Set(
        (layout.tileColors as unknown[]).map(
          (c, i) => (layout.tiles as number[])[i] + JSON.stringify(c),
        ),
      ).size,
      carpetVariants: new Set((layout.carpetTiles as unknown[]).map((c) => JSON.stringify(c))).size,
      admitted: checkLayoutForm(layout) ?? 'admitted',
      ...layoutByteSizes(layout),
    }),
  );
} else if (cmd === 'stage') {
  console.log(JSON.stringify(runStage(a1, a2)));
} else if (cmd === 'all') {
  const results = STAGES.map((s) =>
    JSON.parse(
      execFileSync(process.execPath, [...process.execArgv, __filename, 'stage', s, a1], {
        encoding: 'utf-8',
        maxBuffer: 1 << 20,
      }),
    ),
  );
  console.log(JSON.stringify(results, null, 2));
} else {
  console.error(
    'usage: measure-layout-bounds.ts sizes | write <dir> | stage <name> <dir> | all <dir>',
  );
  process.exit(2);
}
