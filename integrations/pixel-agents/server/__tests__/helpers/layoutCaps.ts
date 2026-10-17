/**
 * Layout builders AT the shared caps (core/src/layoutLimits.ts) for the bounds tests. Every
 * dimension, count and length here is DERIVED from the imported constants (or, for the shipped
 * metropolis grid, from the shipped forest world it is built around) — never a copied number —
 * so the tests follow the ONE definition when it changes.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  LAYOUT_ID_MAX_LENGTH,
  LAYOUT_MAX_AREAS,
  LAYOUT_MAX_CARPET_CELLS,
  LAYOUT_MAX_CARPET_COLOUR_VARIANTS,
  LAYOUT_MAX_CELLS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FURNITURE,
  LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS,
  LAYOUT_MAX_PETS,
  LAYOUT_MAX_TILE_COLOUR_VARIANTS,
  LAYOUT_POSITION_BOUND,
} from '../../../core/src/layoutLimits.js';

export type Layout = Record<string, unknown>;
export interface Shape {
  cols: number;
  rows: number;
}

const REPO = path.join(__dirname, '../../..');

/** The WIDEST grid whose cols x rows is EXACTLY LAYOUT_MAX_CELLS with both axes within
 *  LAYOUT_MAX_DIM (320 x 256 for 81,920 cells and a 384 ceiling). */
export function exactCapShape(): Shape {
  for (let cols = LAYOUT_MAX_DIM; cols >= 1; cols--) {
    const rows = LAYOUT_MAX_CELLS / cols;
    if (Number.isInteger(rows) && rows <= LAYOUT_MAX_DIM) return { cols, rows };
  }
  throw new Error('no grid within LAYOUT_MAX_DIM has exactly LAYOUT_MAX_CELLS cells');
}
export const CAP: Shape = exactCapShape();
/** The widest grid within the cell cap: every column the axis allows (384 x 213). */
export const WIDEST: Shape = {
  cols: LAYOUT_MAX_DIM,
  rows: Math.floor(LAYOUT_MAX_CELLS / LAYOUT_MAX_DIM),
};
/** ...and its transpose, the tallest (213 x 384). */
export const TALLEST: Shape = { cols: WIDEST.rows, rows: WIDEST.cols };
/** Both axes within LAYOUT_MAX_DIM, one row more than WIDEST: over the total-cell cap. */
export const JUST_OVER_CELLS: Shape = { cols: WIDEST.cols, rows: WIDEST.rows + 1 };
/** Both axes AT LAYOUT_MAX_DIM: DIM x DIM is not admitted (cells are capped independently). */
export const DIM_SQUARED: Shape = { cols: LAYOUT_MAX_DIM, rows: LAYOUT_MAX_DIM };

/** The connected metropolis grid the caps were raised for: the shipped 176 x 112 forest as one
 *  quadrant, i.e. twice the forest on each axis (352 x 224). */
export function metropolisShape(): Shape {
  const text = fs.readFileSync(path.join(REPO, 'packaging/di/worlds/vast-forest.json'), 'utf-8');
  const forest = JSON.parse(text) as Shape;
  return { cols: forest.cols * 2, rows: forest.rows * 2 };
}

/** Editor output at its widest JSON spelling (HSBC sliders: h -180..180, s/b/c -100..100). */
export const EDITOR_COLOUR = { h: -180, s: -100, b: -100, c: -100, colorize: true };
/** The longest JSON number spelling the structural check admits (finite, |v| <= the bound). */
export const LONG_NUMBER = -2.2250738585072014e-308;
export const LONG_COLOUR = {
  h: LONG_NUMBER,
  s: LONG_NUMBER,
  b: LONG_NUMBER,
  c: LONG_NUMBER,
  colorize: false,
};
/**
 * The k-th DISTINCT editor colour, as wide as the editor spells it (k = 0 IS EDITOR_COLOUR): h
 * walks -180..-100, then s steps up from -100. Distinct for every k below 81 * 201.
 */
export const variantColour = (k: number) => ({
  h: -180 + (k % 81),
  s: -100 + Math.floor(k / 81),
  b: -100,
  c: -100,
  colorize: true,
});
/** n coloured cells, each a DIFFERENT tile colour variant (64 columns wide). */
export function tileVariants(n: number): Layout {
  const shape = { cols: 64, rows: Math.ceil(n / 64) };
  const layout = tilesOnly(shape);
  return {
    ...layout,
    tileColors: Array.from({ length: shape.cols * shape.rows }, (_, i) =>
      i < n ? variantColour(i) : null,
    ),
  };
}
/** n carpeted cells, each a DIFFERENT carpet colour variant (64 columns wide). */
export function carpetVariants(n: number): Layout {
  const shape = { cols: 64, rows: Math.ceil(n / 64) };
  return {
    ...tilesOnly(shape),
    carpetTiles: Array.from({ length: shape.cols * shape.rows }, (_, i) =>
      i < n ? { variant: 0, color: variantColour(i) } : null,
    ),
  };
}
/** n items of ONE type, each a DIFFERENT colour: n furniture colour variants. */
export function furnitureVariants(n: number): Layout {
  return {
    ...tilesOnly({ cols: 2, rows: 1 }),
    furniture: Array.from({ length: n }, (_, i) => ({
      uid: `v${i}`,
      type: 'DESK_FRONT',
      col: 0,
      row: 0,
      color: variantColour(i),
    })),
  };
}

/* eslint-disable pixel-agents/no-inline-colors -- area colour as layout test data */
export const AREA_COLOUR = '#336699';
/* eslint-enable pixel-agents/no-inline-colors */

/** An ASCII id/label of exactly `length` characters (default: the id cap), unique per prefix. */
export const asciiId = (prefix: string, length = LAYOUT_ID_MAX_LENGTH): string =>
  (prefix + 'x'.repeat(length)).slice(0, length);
/** The control characters JSON.stringify escapes to SIX bytes each (\u0001 .. \u001f, minus the
 *  ones with a two-byte short escape: \b \t \n \f \r). */
const SIX_BYTE = Array.from({ length: 0x1f }, (_, i) => String.fromCharCode(i + 1)).filter(
  (ch) => JSON.stringify(ch).length === 8,
);
/** A label of LAYOUT_ID_MAX_LENGTH characters that serializes to six bytes per character —
 *  structurally a legal id; distinct for every seed below SIX_BYTE.length squared. */
export function heavyLabel(seed = 0): string {
  const n = SIX_BYTE.length;
  return (
    SIX_BYTE[seed % n] +
    SIX_BYTE[Math.floor(seed / n) % n] +
    SIX_BYTE[0].repeat(LAYOUT_ID_MAX_LENGTH - 2)
  );
}

/** cols x rows of floor, no other content: the smallest structurally complete layout of a shape. */
export function tilesOnly({ cols, rows }: Shape, tile = 1): Layout {
  return { version: 1, cols, rows, tiles: new Array(cols * rows).fill(tile), furniture: [] };
}

/**
 * The MAXIMAL legitimate layout (what scripts/measure-layout-bounds.ts measures): every cell
 * coloured at the editor's widest spelling, LAYOUT_MAX_CARPET_CELLS of them carpeted with colour +
 * accent + order (the carpeted-cell cap), and every cell in an area
 * whose label is LAYOUT_ID_MAX_LENGTH ASCII characters; LAYOUT_MAX_FURNITURE coloured items with
 * uid AND type at the character cap and positions at the bound; LAYOUT_MAX_PETS pets and
 * LAYOUT_MAX_AREAS areas with ids/labels at the cap. By default it also sits EXACTLY AT the three
 * distinct colour-variant caps (tile, carpet, furniture); `label`/`colour` grow it for byte tests
 * (a given `colour` is then the ONE colour everywhere).
 */
export function maximalLegitimate(
  { cols, rows }: Shape = CAP,
  options: { label?: (k: number) => string; colour?: Record<string, unknown> } = {},
): Layout {
  const cells = cols * rows;
  const fixed = options.colour;
  // variant 0 is EDITOR_COLOUR itself, so cells past each cap add no further variant
  const tileColour = (i: number) =>
    fixed ?? variantColour(i < LAYOUT_MAX_TILE_COLOUR_VARIANTS ? i : 0);
  const carpetColour = (i: number) =>
    fixed ?? variantColour(i < LAYOUT_MAX_CARPET_COLOUR_VARIANTS ? i : 0);
  // (type, colour) pairs: LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS types, each with ONE colour
  const furnitureVariant = (k: number) => k % LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS;
  const label = options.label ?? ((k: number) => asciiId(`district-${k}-`));
  const areas = Array.from({ length: LAYOUT_MAX_AREAS }, (_, k) => ({
    label: label(k),
    color: AREA_COLOUR,
  }));
  return {
    version: 1,
    cols,
    rows,
    layoutRevision: 1,
    tiles: new Array(cells).fill(7),
    tileColors: Array.from({ length: cells }, (_, i) => tileColour(i)),
    furniture: Array.from({ length: LAYOUT_MAX_FURNITURE }, (_, k) => ({
      uid: asciiId(`uid-${k}-`),
      type: asciiId(`type-${furnitureVariant(k)}-`),
      col: -LAYOUT_POSITION_BOUND,
      row: -LAYOUT_POSITION_BOUND,
      color: fixed ?? variantColour(furnitureVariant(k)),
    })),
    pets: Array.from({ length: LAYOUT_MAX_PETS }, (_, k) => ({
      id: asciiId(`pet-${k}-`),
      petType: 1,
    })),
    // carpeted on exactly LAYOUT_MAX_CARPET_CELLS cells (the carpeted-cell cap)
    carpetTiles: Array.from({ length: cells }, (_, i) =>
      i < LAYOUT_MAX_CARPET_CELLS
        ? { variant: 2, color: carpetColour(i), accentColor: carpetColour(i), order: -1_000_000 }
        : null,
    ),
    areas,
    areaTiles: Array.from({ length: cells }, (_, i) => areas[i % areas.length].label),
  };
}

/**
 * STRUCTURALLY valid at the cell cap (every check but the byte budget passes) and already in the
 * consumer form (migration changes nothing), yet its compact save frame is over
 * LAYOUT_MAX_FRAME_BYTES: a six-byte-escaped label on every cell and the longest colour numbers.
 * Its pretty file stays under LAYOUT_MAX_FILE_BYTES, so a reader reaches the byte check.
 */
export function overBytes(): Layout {
  const { cols, rows } = CAP;
  const cells = cols * rows;
  const label = heavyLabel();
  return {
    version: 1,
    cols,
    rows,
    tiles: new Array(cells).fill(1),
    tileColors: new Array(cells).fill(LONG_COLOUR),
    furniture: [],
    pets: [],
    areas: [{ label, color: AREA_COLOUR }],
    areaTiles: new Array(cells).fill(label),
  };
}

/**
 * Binary search: the LARGEST n in [lo, hi) for which `admitted(n)` holds, given that it holds at
 * lo and fails at hi (both asserted, so the search is well-founded), assuming monotonicity.
 */
export function largestAdmitted(lo: number, hi: number, admitted: (n: number) => boolean): number {
  if (!admitted(lo)) throw new Error(`boundary search: not admitted at lo=${lo}`);
  if (admitted(hi)) throw new Error(`boundary search: still admitted at hi=${hi}`);
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    if (admitted(mid)) lo = mid;
    else hi = mid;
  }
  return lo;
}

/** The reason checkLayoutForm gives for a grid over the total-cell cap. */
export const cellCapReason = ({ cols, rows }: Shape): string =>
  `layout cols x rows is ${cols * rows} cells, over ${LAYOUT_MAX_CELLS}`;
