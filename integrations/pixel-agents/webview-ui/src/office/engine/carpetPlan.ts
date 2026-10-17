/**
 * The CARPET JUNCTION PLAN, kept DOM-free (no canvas types) so the Node test project can import it;
 * the renderer draws from it (renderer.ts: the pre-composed carpet layer and the per-junction path).
 */
import type { ColorValue } from '../../components/ui/types.js';
import { CARPET_DEFAULT_ACCENT_COLOR, CARPET_DEFAULT_COLOR } from '../../constants.js';
import { presentationGeneration } from '../presentationGeneration.js';
import { getCarpetJunctionSprite, getCarpetPaletteKey } from '../sprites/carpetTiles.js';
import type { CarpetTile, SpriteData } from '../types.js';

/**
 * CARPET JUNCTION PLAN — the carpet layer is STATIC: what each junction draws depends only on the
 * carpetTiles array, the grid shape and the carpet sprite set. So the per-junction resolution
 * (palette keys, the order-merged groups, the marching-squares case, the colorized sprite) runs
 * ONCE per plan, and a frame only walks integer offsets and draws — no Map, no string key, no
 * sort per frame.
 *
 * Invalidated by EXACTLY: a different carpetTiles ARRAY (a layout load, and every editor carpet
 * paint / erase and grid expansion — editorActions copy that array, never mutate it in place),
 * a different cols or rows, and presentationGeneration() (setCarpetSprites: the carpet sprite set
 * loaded or reloaded). Bounded: ONE plan is kept; it holds (cols + 1) x (rows + 1) + 1 int32
 * offsets and at most four sprite references per junction that touches a carpet (carpeted cells
 * are capped by LAYOUT_MAX_CARPET_CELLS).
 *
 * @internal
 */
export interface CarpetJunctionPlan {
  tiles: Array<CarpetTile | null>;
  cols: number;
  rows: number;
  generation: number;
  /** sprites[offsets[j] .. offsets[j + 1]) are junction j's layers (j = jy * (cols + 1) + jx),
   *  lowest order first. */
  offsets: Int32Array;
  sprites: SpriteData[];
  /** The junctions that draw anything: inclusive bounds (null when nothing draws). */
  bounds: { jx0: number; jy0: number; jx1: number; jy1: number } | null;
}
let carpetPlan: CarpetJunctionPlan | null = null;

/** Build (or reuse) the plan for these carpets. @internal */
export function carpetJunctionPlan(
  carpetTiles: Array<CarpetTile | null>,
  cols: number,
  rows: number,
): CarpetJunctionPlan {
  const generation = presentationGeneration();
  if (
    carpetPlan &&
    carpetPlan.tiles === carpetTiles &&
    carpetPlan.cols === cols &&
    carpetPlan.rows === rows &&
    carpetPlan.generation === generation
  )
    return carpetPlan;
  const jw = cols + 1;
  const junctions = jw * (rows + 1);
  const offsets = new Int32Array(junctions + 1);
  const sprites: SpriteData[] = [];
  let bounds: CarpetJunctionPlan['bounds'] = null;
  // a junction draws only if one of its four cells is carpeted
  const carpeted = (col: number, row: number): boolean =>
    col >= 0 && row >= 0 && col < cols && row < rows && !!carpetTiles[row * cols + col];
  for (let jy = 0; jy <= rows; jy++) {
    for (let jx = 0; jx <= cols; jx++) {
      offsets[jy * jw + jx] = sprites.length;
      if (
        !carpeted(jx - 1, jy - 1) &&
        !carpeted(jx, jy - 1) &&
        !carpeted(jx, jy) &&
        !carpeted(jx - 1, jy)
      )
        continue;
      const localGroups = new Map<
        string,
        {
          variant: number;
          color: ColorValue;
          accentColor: ColorValue;
          paletteKey: string;
          order: number;
        }
      >();

      const adjacent = [
        { col: jx - 1, row: jy - 1 }, // NW
        { col: jx, row: jy - 1 }, // NE
        { col: jx, row: jy }, // SE
        { col: jx - 1, row: jy }, // SW
      ];

      for (const pos of adjacent) {
        if (pos.col < 0 || pos.row < 0 || pos.col >= cols || pos.row >= rows) continue;
        const tile = carpetTiles[pos.row * cols + pos.col];
        if (!tile) continue;
        const color = tile.color ?? CARPET_DEFAULT_COLOR;
        const accentColor = tile.accentColor ?? CARPET_DEFAULT_ACCENT_COLOR;
        const paletteKey = getCarpetPaletteKey(color, accentColor);
        const key = `${tile.variant}:${paletteKey}`;
        const order = tile.order ?? 0;
        const existing = localGroups.get(key);
        if (!existing || order > existing.order) {
          localGroups.set(key, { variant: tile.variant, color, accentColor, paletteKey, order });
        }
      }

      // Ascending order → drawn lowest first; highest-order layer ends on top.
      const ordered = [...localGroups.values()].sort((a, b) => a.order - b.order);
      for (const { variant, color, accentColor, paletteKey } of ordered) {
        const sprite = getCarpetJunctionSprite(
          jx,
          jy,
          variant,
          carpetTiles,
          cols,
          rows,
          color,
          accentColor,
          paletteKey,
        );
        if (!sprite) continue;
        sprites.push(sprite);
        if (!bounds) bounds = { jx0: jx, jy0: jy, jx1: jx, jy1: jy };
        else {
          bounds.jx0 = Math.min(bounds.jx0, jx);
          bounds.jy0 = Math.min(bounds.jy0, jy);
          bounds.jx1 = Math.max(bounds.jx1, jx);
          bounds.jy1 = Math.max(bounds.jy1, jy);
        }
      }
    }
  }
  offsets[junctions] = sprites.length;
  carpetPlan = { tiles: carpetTiles, cols, rows, generation, offsets, sprites, bounds };
  return carpetPlan;
}

/** The render-cache lifecycle (office/renderCaches.ts): drop the plan, so it pins no sprite of a
 *  layout that is gone. */
export function resetCarpetPlan(): void {
  carpetPlan = null;
}
