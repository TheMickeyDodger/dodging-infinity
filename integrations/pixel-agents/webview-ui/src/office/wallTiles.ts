/**
 * Wall tile auto-tiling: sprite storage and bitmask-based piece selection.
 *
 * Stores wall tile sets loaded from individual PNGs in assets/walls/.
 * Each set contains 16 wall sprites (one per 4-bit bitmask).
 * At render time, each wall tile's 4 cardinal neighbors are checked to build
 * a bitmask, and the corresponding sprite is drawn directly.
 * No changes to the layout model — auto-tiling is purely visual.
 *
 * Bitmask convention: N=1, E=2, S=4, W=8. Out-of-bounds = NOT wall.
 */

import type { ColorValue } from '../components/ui/types.js';
import { getColorizedSprite } from './colorize.js';
import { bumpPresentationGeneration } from './presentationGeneration.js';
import type { FurnitureInstance, SpriteData, TileType as TileTypeVal } from './types.js';
import { TILE_SIZE, TileType } from './types.js';

/** Wall tile sets: each set has 16 sprites indexed by bitmask (0-15) */
let wallSets: SpriteData[][] = [];

/** Set wall tile sets (called once when extension sends wallTilesLoaded) */
export function setWallSprites(sets: SpriteData[][]): void {
  bumpPresentationGeneration();
  wallSets = sets;
  seamFreeCache.clear();
}

/** Check if wall sprites have been loaded */
export function hasWallSprites(): boolean {
  return wallSets.length > 0;
}

/** Get number of available wall sets */
export function getWallSetCount(): number {
  return wallSets.length;
}

/** Get the first sprite (bitmask 0, top-left piece) of a wall set for preview rendering */
export function getWallSetPreviewSprite(setIndex: number): SpriteData | null {
  const set = wallSets[setIndex];
  if (!set) return null;
  return set[0] ?? null;
}

/**
 * Build the 4-bit neighbor bitmask for a wall tile at (col, row).
 */
function buildWallMask(col: number, row: number, tileMap: TileTypeVal[][]): number {
  const tmRows = tileMap.length;
  const tmCols = tmRows > 0 ? tileMap[0].length : 0;

  let mask = 0;
  if (row > 0 && tileMap[row - 1][col] === TileType.WALL) mask |= 1; // N
  if (col < tmCols - 1 && tileMap[row][col + 1] === TileType.WALL) mask |= 2; // E
  if (row < tmRows - 1 && tileMap[row + 1][col] === TileType.WALL) mask |= 4; // S
  if (col > 0 && tileMap[row][col - 1] === TileType.WALL) mask |= 8; // W
  return mask;
}

/**
 * THICK-WALL SEAMS AND PINHOLES. The 16 pieces draw dark outline pixels on their east and west edges:
 *  - a side line from row 8 down on pieces with a south neighbour plus an east (or west) one;
 *  - corner pixels on row 0 on pieces with a north neighbour.
 * Both are correct where the wall is ONE tile thick (they mark a face boundary). Where the wall mass
 * continues on that side they land INSIDE the mass, at every tile boundary:
 *  - the "regular black vertical marks" the human reported;
 *  - the scattered black pinholes the Operator found on pale walls.
 * So an outline pixel on the east edge is filled with the face pixel beside it when the mass continues
 * east at that height: rows 0-7 when the north, north-east and east cells are walls; rows 8 and below
 * when the south, south-east and east cells are walls. The west edge is mirrored. Only isolated edge
 * pixels change (an outline whose inner neighbour is face), never a horizontal edge line, and
 * single-thickness walls are unchanged. Purely visual; the layout is untouched.
 */
interface Thick {
  e: boolean;
  w: boolean;
  ne: boolean;
  nw: boolean;
}
function thickSides(col: number, row: number, tileMap: TileTypeVal[][]): Thick {
  const rows = tileMap.length;
  const cols = rows > 0 ? tileMap[0].length : 0;
  const wall = (c: number, r: number) =>
    r >= 0 && r < rows && c >= 0 && c < cols && tileMap[r][c] === TileType.WALL;
  const east = wall(col + 1, row);
  const west = wall(col - 1, row);
  const south = wall(col, row + 1);
  const north = wall(col, row - 1);
  return {
    e: south && east && wall(col + 1, row + 1),
    w: south && west && wall(col - 1, row + 1),
    ne: north && east && wall(col + 1, row - 1),
    nw: north && west && wall(col - 1, row - 1),
  };
}
const thickKey = (t: Thick) => `${t.e ? 'E' : ''}${t.w ? 'W' : ''}${t.ne ? 'n' : ''}${t.nw ? 'm' : ''}`;

const seamFreeCache = new Map<string, SpriteData>();
const CAP_ROWS = 8; // rows 0-7: the cap region that meets the north neighbour

function isOutline(px: string): boolean {
  if (!px || px.length < 7) return false;
  const r = parseInt(px.slice(1, 3), 16);
  const g = parseInt(px.slice(3, 5), 16);
  const b = parseInt(px.slice(5, 7), 16);
  return r + g + b < 200;
}

/** The piece with its interior edge outlines replaced by the adjacent face pixel (see above). */
function seamFree(sprite: SpriteData, key: string, t: Thick): SpriteData {
  if (!t.e && !t.w && !t.ne && !t.nw) return sprite;
  const cacheKey = `${key}-${thickKey(t)}`;
  const hit = seamFreeCache.get(cacheKey);
  if (hit) return hit;
  const out = sprite.map((row) => row.slice());
  const width = out[0]?.length ?? 0;
  for (let y = 0; y < out.length; y++) {
    const e = y < CAP_ROWS ? t.ne : t.e;
    const w = y < CAP_ROWS ? t.nw : t.w;
    if (e && isOutline(out[y][width - 1]) && !isOutline(out[y][width - 2]))
      out[y][width - 1] = out[y][width - 2];
    if (w && isOutline(out[y][0]) && !isOutline(out[y][1])) out[y][0] = out[y][1];
  }
  seamFreeCache.set(cacheKey, out);
  return out;
}

/**
 * Get the wall sprite for a tile based on its cardinal neighbors.
 * Returns the sprite + Y offset, or null to fall back to solid WALL_COLOR.
 */
function getWallSprite(
  col: number,
  row: number,
  tileMap: TileTypeVal[][],
  setIndex = 0,
): { sprite: SpriteData; offsetY: number } | null {
  if (wallSets.length === 0) return null;
  const sprites = wallSets[setIndex] ?? wallSets[0];

  const mask = buildWallMask(col, row, tileMap);
  const base = sprites[mask];
  if (!base) return null;
  const thick = thickSides(col, row, tileMap);
  const sprite = seamFree(base, `wall-${setIndex}-${mask}`, thick);

  // Anchor sprite at bottom of tile — tall sprites extend upward
  return { sprite, offsetY: TILE_SIZE - sprite.length };
}

/**
 * Get a colorized wall sprite for a tile based on its cardinal neighbors.
 * Uses Colorize mode (grayscale → HSL) like floor tiles.
 * Returns the colorized sprite + Y offset, or null if no wall sprites loaded.
 */
function getColorizedWallSprite(
  col: number,
  row: number,
  tileMap: TileTypeVal[][],
  color: ColorValue,
  setIndex = 0,
): { sprite: SpriteData; offsetY: number } | null {
  if (wallSets.length === 0) return null;
  const sprites = wallSets[setIndex] ?? wallSets[0];

  const mask = buildWallMask(col, row, tileMap);
  const base = sprites[mask];
  if (!base) return null;
  const thick = thickSides(col, row, tileMap);
  const sprite = seamFree(base, `wall-${setIndex}-${mask}`, thick);

  const cacheKey = `wall-${setIndex}-${mask}-${thickKey(thick)}-${color.h}-${color.s}-${color.b}-${color.c}`;
  const colorized = getColorizedSprite(cacheKey, sprite, { ...color, colorize: true });

  return { sprite: colorized, offsetY: TILE_SIZE - sprite.length };
}

/**
 * Build FurnitureInstance-like objects for all wall tiles so they can participate
 * in z-sorting with furniture and characters.
 */
export function getWallInstances(
  tileMap: TileTypeVal[][],
  tileColors?: Array<ColorValue | null>,
  cols?: number,
): FurnitureInstance[] {
  if (wallSets.length === 0) return [];
  const tmRows = tileMap.length;
  const tmCols = tmRows > 0 ? tileMap[0].length : 0;
  const layoutCols = cols ?? tmCols;
  const instances: FurnitureInstance[] = [];
  for (let r = 0; r < tmRows; r++) {
    for (let c = 0; c < tmCols; c++) {
      if (tileMap[r][c] !== TileType.WALL) continue;
      const colorIdx = r * layoutCols + c;
      const wallColor = tileColors?.[colorIdx];
      const wallInfo = wallColor
        ? getColorizedWallSprite(c, r, tileMap, wallColor)
        : getWallSprite(c, r, tileMap);
      if (!wallInfo) continue;
      instances.push({
        sprite: wallInfo.sprite,
        x: c * TILE_SIZE,
        y: r * TILE_SIZE + wallInfo.offsetY,
        zY: (r + 1) * TILE_SIZE,
      });
    }
  }
  return instances;
}

/**
 * Compute the flat fill hex color for a wall tile with a given ColorValue.
 * Uses same Colorize algorithm as floor tiles: 50% gray → HSL.
 */
export function wallColorToHex(color: ColorValue): string {
  const { h, s, b, c } = color;
  // Start with 50% gray (wall base)
  let lightness = 0.5;

  // Apply contrast
  if (c !== 0) {
    const factor = (100 + c) / 100;
    lightness = 0.5 + (lightness - 0.5) * factor;
  }

  // Apply brightness
  if (b !== 0) {
    lightness = lightness + b / 200;
  }

  lightness = Math.max(0, Math.min(1, lightness));

  // HSL to hex (same as colorize.ts hslToHex)
  const satFrac = s / 100;
  const ch = (1 - Math.abs(2 * lightness - 1)) * satFrac;
  const hp = h / 60;
  const x = ch * (1 - Math.abs((hp % 2) - 1));
  let r1: number;
  let g1: number;
  let b1: number;

  if (hp < 1) {
    r1 = ch;
    g1 = x;
    b1 = 0;
  } else if (hp < 2) {
    r1 = x;
    g1 = ch;
    b1 = 0;
  } else if (hp < 3) {
    r1 = 0;
    g1 = ch;
    b1 = x;
  } else if (hp < 4) {
    r1 = 0;
    g1 = x;
    b1 = ch;
  } else if (hp < 5) {
    r1 = x;
    g1 = 0;
    b1 = ch;
  } else {
    r1 = ch;
    g1 = 0;
    b1 = x;
  }

  const m = lightness - ch / 2;
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round((v + m) * 255)));

  return `#${clamp(r1).toString(16).padStart(2, '0')}${clamp(g1).toString(16).padStart(2, '0')}${clamp(b1).toString(16).padStart(2, '0')}`;
}
