/**
 * WHAT IS OUTDOORS, AND WHERE THE LIGHTS ARE (living city 6–9): two derived, cached views of a layout
 * that the lighting and weather layers read. DOM-free.
 *
 * - The MASK classes every tile as nothing (outside the map), OUTDOORS (streets, grass, water, sand,
 *   plazas, crops) or INDOORS (walls, the office/hall/gallery floors). Night darkens the outdoors and
 *   leaves the indoors lit; weather is drawn over the outdoors only. It is read from the tile TYPES the
 *   world already has, so no world data changes.
 * - The LAMPS are the furniture that gives light, placed by their own footprints.
 *
 * Both are cached by the identity of the layout arrays they came from (a layout edit replaces them), and
 * the lamps also by the presentation generation (the footprints come from the furniture catalogue).
 */
import { CITY_LIGHT_SOURCE_TYPES } from '../../constants.js';
import { presentationGeneration } from '../presentationGeneration.js';
import { TILE_SIZE, TileType } from '../types.js';

export const MASK_NONE = 0;
export const MASK_OUTDOOR = 1;
export const MASK_INDOOR = 2;

/** Tile types that are indoors: building walls, the numbered indoor floors, halls/galleries, offices. */
const INDOOR_TILE_TYPES: ReadonlySet<number> = new Set<number>([
  TileType.WALL,
  TileType.FLOOR_1,
  TileType.FLOOR_2,
  TileType.FLOOR_3,
  TileType.FLOOR_4,
  TileType.FLOOR_5,
  TileType.FLOOR_6,
  TileType.FLOOR_7,
  TileType.FLOOR_8,
  TileType.FLOOR_9,
  TileType.FLOOR_25,
  TileType.FLOOR_26,
]);

export interface CityMask {
  cols: number;
  rows: number;
  /** cols * rows cells, row-major: MASK_NONE | MASK_OUTDOOR | MASK_INDOOR. */
  cells: Uint8Array;
}

export function classifyTile(tile: number): number {
  if (tile === TileType.VOID) return MASK_NONE;
  return INDOOR_TILE_TYPES.has(tile) ? MASK_INDOOR : MASK_OUTDOOR;
}

export function buildCityMask(cols: number, rows: number, tiles: readonly number[]): CityMask {
  const cells = new Uint8Array(cols * rows);
  const n = Math.min(cells.length, tiles.length);
  for (let i = 0; i < n; i++) cells[i] = classifyTile(tiles[i]);
  // A wall sprite is drawn with a raised face covering the tile ABOVE it, so an outdoor tile directly
  // north of a wall is that building's lit facade, not street: night must not leave a dark band there.
  for (let i = cols; i < n; i++) {
    if (tiles[i] === TileType.WALL && cells[i - cols] === MASK_OUTDOOR)
      cells[i - cols] = MASK_INDOOR;
  }
  return { cols, rows, cells };
}

const maskCache = new WeakMap<object, CityMask>();

/** The mask of a layout, built once per tile array. */
export function cityMaskFor(layout: {
  cols: number;
  rows: number;
  tiles: readonly number[];
}): CityMask {
  const cached = maskCache.get(layout.tiles);
  if (cached && cached.cols === layout.cols && cached.rows === layout.rows) return cached;
  const built = buildCityMask(layout.cols, layout.rows, layout.tiles);
  maskCache.set(layout.tiles, built);
  return built;
}

export function maskAt(mask: CityMask, col: number, row: number): number {
  if (col < 0 || row < 0 || col >= mask.cols || row >= mask.rows) return MASK_NONE;
  return mask.cells[row * mask.cols + col];
}

/** Calls `emit(row, firstCol, endCol)` for every maximal run of INDOOR cells in rows [r0, r1) inside columns [c0, c1). */
export function indoorRuns(
  mask: CityMask,
  c0: number,
  c1: number,
  r0: number,
  r1: number,
  emit: (row: number, from: number, to: number) => void,
): void {
  const lo = Math.max(0, c0);
  const hi = Math.min(mask.cols, c1);
  for (let r = Math.max(0, r0); r < Math.min(mask.rows, r1); r++) {
    const base = r * mask.cols;
    let start = -1;
    for (let c = lo; c < hi; c++) {
      const indoor = mask.cells[base + c] === MASK_INDOOR;
      if (indoor && start < 0) start = c;
      else if (!indoor && start >= 0) {
        emit(r, start, c);
        start = -1;
      }
    }
    if (start >= 0) emit(r, start, hi);
  }
}

/** A light's centre in world pixels. */
export interface LightPoint {
  x: number;
  y: number;
}

export type FootprintOf = (type: string) => { w: number; h: number } | null;

interface LampCacheEntry {
  generation: number;
  lamps: readonly LightPoint[];
}
const lampCache = new WeakMap<object, LampCacheEntry>();
const LIGHT_TYPES: ReadonlySet<string> = new Set(CITY_LIGHT_SOURCE_TYPES);

/** The street lamps and lanterns of a layout, at the middle of their footprint's top row. */
export function lampLights(
  layout: { furniture: readonly { type: string; col: number; row: number }[] },
  footprintOf: FootprintOf,
): readonly LightPoint[] {
  const generation = presentationGeneration();
  const cached = lampCache.get(layout.furniture);
  if (cached && cached.generation === generation) return cached.lamps;
  const lamps: LightPoint[] = [];
  for (const f of layout.furniture) {
    if (!LIGHT_TYPES.has(f.type)) continue;
    const fp = footprintOf(f.type) ?? { w: 1, h: 1 };
    lamps.push({ x: (f.col + fp.w / 2) * TILE_SIZE, y: (f.row + 0.5) * TILE_SIZE });
  }
  lampCache.set(layout.furniture, { generation, lamps });
  return lamps;
}
