import type { ColorValue } from '../../components/ui/types.js';
import {
  AMBIENT_BADGE_H,
  AMBIENT_BADGE_OFFSET_Y,
  AMBIENT_BADGE_W,
  AMBIENT_JOB_COLORS,
  AMBIENT_LABEL_BG,
  AMBIENT_LABEL_FG,
  AMBIENT_LABEL_FONT_PX,
  AREA_ACTIVE_ALPHA_MULTIPLIER,
  AREA_LABEL_ALPHA,
  AREA_LABEL_FALLBACK_COLOR,
  AREA_LABEL_FONT_SIZE_PX,
  AREA_LABEL_MIN_FONT_SIZE_PX,
  AREA_LABEL_SHADOW_ALPHA,
  AREA_LABEL_SHADOW_COLOR,
  AREA_OVERLAY_ALPHA,
  BUBBLE_FADE_DURATION_SEC,
  BUBBLE_SITTING_OFFSET_PX,
  BUBBLE_VERTICAL_OFFSET_PX,
  BUTTON_ICON_COLOR,
  BUTTON_ICON_SIZE_FACTOR,
  BUTTON_LINE_WIDTH_MIN,
  BUTTON_LINE_WIDTH_ZOOM_FACTOR,
  BUTTON_MIN_RADIUS,
  BUTTON_RADIUS_ZOOM_FACTOR,
  CARPET_LAYER_MAX_PIXELS,
  CHARACTER_SITTING_OFFSET_PX,
  CHARACTER_Z_SORT_OFFSET,
  DELETE_BUTTON_BG,
  FALLBACK_FLOOR_COLOR,
  GHOST_BORDER_HOVER_FILL,
  GHOST_BORDER_HOVER_STROKE,
  GHOST_BORDER_STROKE,
  GHOST_INVALID_TINT,
  GHOST_PREVIEW_SPRITE_ALPHA,
  GHOST_PREVIEW_TINT_ALPHA,
  GHOST_VALID_TINT,
  GRID_LINE_COLOR,
  HALO_AURA_RADIUS_PX,
  HEADLESS_CHARACTER_ALPHA,
  HOVERED_OUTLINE_ALPHA,
  OUTLINE_Z_SORT_OFFSET,
  ROTATE_BUTTON_BG,
  SEAT_AVAILABLE_COLOR,
  SEAT_BUSY_COLOR,
  SEAT_OWN_COLOR,
  SELECTED_OUTLINE_ALPHA,
  SELECTION_DASH_PATTERN,
  SELECTION_HIGHLIGHT_COLOR,
  VOID_TILE_DASH_PATTERN,
  VOID_TILE_OUTLINE_COLOR,
} from '../../constants.js';
import { ambientLabel } from '../ambient/ambientJobs.js';
import { ambientFrame, ambientSheetFor } from '../ambient/ambientSprites.js';
import type { AmbientResident } from '../ambient/ambientWorld.js';
import { getColorizedFloorSprite, hasFloorSprites, WALL_COLOR } from '../floorTiles.js';
import { getCatalogEntry } from '../layout/furnitureCatalog.js';
import type { VisibleTiles } from '../projection.js';
import { mapOffset, visibleTiles } from '../projection.js';
import { registerRenderCache } from '../renderCaches.js';
import { hasCarpetSprites } from '../sprites/carpetTiles.js';
import { getPetSprites } from '../sprites/petSpriteData.js';
import { getAuraSprite, getCachedSprite, getOutlineSprite } from '../sprites/spriteCache.js';
import {
  BUBBLE_HEART_SPRITE,
  BUBBLE_PERMISSION_SPRITE,
  BUBBLE_WAITING_SPRITE,
  getCharacterSprites,
} from '../sprites/spriteData.js';
import type { Train } from '../transit/transitWorld.js';
import { TRAIN_CARS } from '../transit/transitWorld.js';
import type {
  AreaDefinition,
  CarpetTile,
  Character,
  FurnitureInstance,
  Pet,
  Seat,
  SpriteData,
  TileType as TileTypeVal,
} from '../types.js';
import { CharacterState, TILE_SIZE, TileType } from '../types.js';
import { getWallInstances, hasWallSprites, wallColorToHex } from '../wallTiles.js';
import type { CarpetJunctionPlan } from './carpetPlan.js';
import { carpetJunctionPlan, resetCarpetPlan } from './carpetPlan.js';
import { getCharacterSprite } from './characters.js';
import { renderMatrixEffect } from './matrixEffect.js';
import { getPetSpriteData } from './petEntity.js';

// ── Settings ────────────────────────────────────────────────────

/**
 * "Display headless as ghosts" — whether headless agents render translucent.
 * Module state rather than a render param: the rAF loop reads it every frame,
 * so a toggle takes effect on the next one without threading a 21st argument
 * through renderFrame. Same shape as setProviderCapabilities / setSoundEnabled.
 * Mirrors the server default (off) — the cue is opt-in, so an office looks the
 * same as it did before the setting existed until someone turns it on.
 */
let ghostHeadlessAgents = false;

export function setGhostHeadlessAgents(enabled: boolean): void {
  ghostHeadlessAgents = enabled;
}

export function isGhostHeadlessAgentsEnabled(): boolean {
  return ghostHeadlessAgents;
}

// ── Render functions ────────────────────────────────────────────

/**
 * THE PRE-COMPOSED CARPET LAYER: every planned junction sprite drawn ONCE into one offscreen
 * canvas at BASE resolution (one canvas pixel per sprite pixel) spanning the plan's bounds; every
 * frame, at ANY zoom, blits only the part in view, scaled by the integer zoom with smoothing off
 * — one drawImage instead of up to four per junction. (Measured: the per-junction draws, not the
 * key building, were the cost — measurements/ablation-dpr2-*.)
 * Invalidated by EXACTLY: a different plan (so everything that invalidates the plan, above).
 * Zoom changes do NOT rebuild it. Bounded: ONE layer is kept (its canvas element is reused) and
 * it is at most (cols + 1) x (rows + 1) x 256 pixels — for any ADMITTED grid at most 21.1 M px
 * (384 x 213 cells), within CARPET_LAYER_MAX_PIXELS, so the guard never sends an admitted layout
 * to the direct path. It is part of the render-cache lifecycle (office/renderCaches.ts:
 * resetCarpetLayer). Pixel-identical to direct drawing: nearest-neighbour integer scaling of the
 * same sprites, drawn in the same order at integer positions.
 */
interface CarpetLayer {
  plan: CarpetJunctionPlan;
  canvas: HTMLCanvasElement;
}
let carpetLayer: CarpetLayer | null = null;
let carpetLayerCanvas: HTMLCanvasElement | null = null;
let carpetLayerEnabled = true;
let carpetLayerBuildCount = 0;

/** e2e: switch the pre-composed layer off and on, to compare its pixels with direct drawing.
 *  @internal */
export function setCarpetLayerCacheEnabled(enabled: boolean): void {
  carpetLayerEnabled = enabled;
}

/** @internal */
export function carpetLayerBuilds(): number {
  return carpetLayerBuildCount;
}
let carpetLayerBlitCount = 0;
/** Frames (or probe renders) that drew the carpets FROM the layer. @internal */
export function carpetLayerBlits(): number {
  return carpetLayerBlitCount;
}

/** Pixels held by the carpet layer (render-cache accounting). */
export function carpetLayerPixels(): number {
  return carpetLayer ? carpetLayer.canvas.width * carpetLayer.canvas.height : 0;
}

/** The render-cache lifecycle: drop the junction plan and the carpet layer (its backing store
 *  released), so neither pins sprites or pixels of a layout that is gone. */
export function resetCarpetLayer(): void {
  resetCarpetPlan();
  carpetLayer = null;
  if (carpetLayerCanvas) {
    carpetLayerCanvas.width = 0;
    carpetLayerCanvas.height = 0;
  }
}

// the layer joins the render-cache lifecycle (renderCaches.ts does not import this DOM module)
registerRenderCache({ reset: resetCarpetLayer, pixels: carpetLayerPixels });

function carpetLayerFor(plan: CarpetJunctionPlan): CarpetLayer | null {
  const b = plan.bounds;
  if (!b || !carpetLayerEnabled || typeof document === 'undefined') return null;
  if (carpetLayer && carpetLayer.plan === plan) return carpetLayer;
  const w = (b.jx1 - b.jx0 + 1) * TILE_SIZE;
  const h = (b.jy1 - b.jy0 + 1) * TILE_SIZE;
  if (w * h > CARPET_LAYER_MAX_PIXELS) return null;
  const canvas = (carpetLayerCanvas ??= document.createElement('canvas'));
  canvas.width = w;
  canvas.height = h;
  const c = canvas.getContext('2d');
  if (!c) return null;
  c.imageSmoothingEnabled = false;
  const jw = plan.cols + 1;
  for (let jy = b.jy0; jy <= b.jy1; jy++) {
    for (let jx = b.jx0; jx <= b.jx1; jx++) {
      const j = jy * jw + jx;
      const end = plan.offsets[j + 1];
      for (let k = plan.offsets[j]; k < end; k++)
        c.drawImage(
          getCachedSprite(plan.sprites[k], 1),
          (jx - b.jx0) * TILE_SIZE,
          (jy - b.jy0) * TILE_SIZE,
        );
    }
  }
  carpetLayerBuildCount++;
  carpetLayer = { plan, canvas };
  return carpetLayer;
}

/**
 * Render the carpet layer. Called AFTER renderTileGrid and BEFORE seat
 * indicators / characters / furniture.
 *
 * Per junction (cols+1 × rows+1 grid of corners), we gather every
 * (variant, palette) pair from the up-to-4 adjacent tiles. Each pair becomes
 * one local "layer" drawn in ascending `order`, so the highest-order carpet
 * visually wins at overlaps. Sprite anchor: each 16×16 junction sprite is
 * centered on the corner — drawn at (offsetX + jx*s - halfS, offsetY + jy*s - halfS).
 * The layers are resolved once per carpetJunctionPlan (above); a frame draws them.
 *
 * @internal
 */
export function renderCarpetLayer(
  ctx: CanvasRenderingContext2D,
  carpetTiles: Array<CarpetTile | null>,
  cols: number,
  rows: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
  view?: VisibleTiles,
): void {
  if (!hasCarpetSprites()) return;
  if (!carpetTiles || carpetTiles.length === 0) return;
  const plan = carpetJunctionPlan(carpetTiles, cols, rows);
  if (plan.sprites.length === 0) return;

  const s = TILE_SIZE * zoom;
  const halfS = s / 2;
  const jw = cols + 1;
  const { offsets, sprites } = plan;

  const layer = carpetLayerFor(plan);
  if (layer && plan.bounds) {
    // layer pixel (0, 0) is the top-left of junction (jx0, jy0)'s sprite; on screen it lands at
    // (ox, oy) and each layer pixel covers zoom x zoom device pixels. Blit only what is in view,
    // cropped on whole layer pixels so every destination edge stays on the integer grid.
    const ox = offsetX + plan.bounds.jx0 * s - halfS;
    const oy = offsetY + plan.bounds.jy0 * s - halfS;
    const sx = Math.max(0, Math.floor(-ox / zoom));
    const sy = Math.max(0, Math.floor(-oy / zoom));
    const ex = Math.min(
      layer.canvas.width,
      Math.ceil(((ctx.canvas?.width ?? Infinity) - ox) / zoom),
    );
    const ey = Math.min(
      layer.canvas.height,
      Math.ceil(((ctx.canvas?.height ?? Infinity) - oy) / zoom),
    );
    carpetLayerBlitCount++;
    if (ex > sx && ey > sy)
      ctx.drawImage(
        layer.canvas,
        sx,
        sy,
        ex - sx,
        ey - sy,
        ox + sx * zoom,
        oy + sy * zoom,
        (ex - sx) * zoom,
        (ey - sy) * zoom,
      );
    return;
  }

  // Junctions are tile CORNERS: the visible range's corners run c0..c1 / r0..r1 inclusive.
  const jy0 = Math.max(0, view ? view.r0 : 0);
  const jy1 = Math.min(rows, view ? view.r1 : rows);
  const jx0 = Math.max(0, view ? view.c0 : 0);
  const jx1 = Math.min(cols, view ? view.c1 : cols);
  for (let jy = jy0; jy <= jy1; jy++) {
    for (let jx = jx0; jx <= jx1; jx++) {
      const j = jy * jw + jx;
      const end = offsets[j + 1];
      for (let k = offsets[j]; k < end; k++) {
        const cached = getCachedSprite(sprites[k], zoom);
        ctx.drawImage(cached, offsetX + jx * s - halfS, offsetY + jy * s - halfS);
      }
    }
  }
}

/**
 * Translucent per-tile color wash for Areas. Runs ABOVE carpets/floor and
 * BELOW seat indicators / characters / furniture. The active area (the one
 * the editor has currently selected) gets a multiplier-bumped alpha so users
 * can see which area they're editing without changing every other area's
 * visibility.
 *
 * @internal
 */
export function renderAreaOverlay(
  ctx: CanvasRenderingContext2D,
  areaTiles: Array<string | null> | undefined,
  areas: AreaDefinition[] | undefined,
  cols: number,
  rows: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
  activeAreaLabel?: string | null,
  view?: VisibleTiles,
): void {
  if (!areaTiles || areaTiles.length === 0) return;
  if (!areas || areas.length === 0) return;

  const s = TILE_SIZE * zoom;
  const colorMap = new Map<string, string>();
  for (const a of areas) colorMap.set(a.label, a.color);

  ctx.save();
  for (let r = view ? view.r0 : 0; r < (view ? view.r1 : rows); r++) {
    for (let c = view ? view.c0 : 0; c < (view ? view.c1 : cols); c++) {
      const label = areaTiles[r * cols + c];
      if (!label) continue;
      const color = colorMap.get(label);
      if (!color) continue;
      ctx.globalAlpha =
        activeAreaLabel === label
          ? AREA_OVERLAY_ALPHA * AREA_ACTIVE_ALPHA_MULTIPLIER
          : AREA_OVERLAY_ALPHA;
      ctx.fillStyle = color;
      ctx.fillRect(offsetX + c * s, offsetY + r * s, s, s);
    }
  }
  ctx.restore();
}

/**
 * Render the centroid label for each Area, ABOVE characters/bubbles. Centroid
 * = arithmetic mean of all tile centers belonging to a given label. Pixel-art
 * drop shadow (no blur) for legibility on light backgrounds.
 *
 * @internal
 */
export function renderAreaLabels(
  ctx: CanvasRenderingContext2D,
  areaTiles: Array<string | null> | undefined,
  areas: AreaDefinition[] | undefined,
  cols: number,
  rows: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  if (!areaTiles || areaTiles.length === 0) return;
  if (!areas || areas.length === 0) return;

  const s = TILE_SIZE * zoom;
  const colorMap = new Map<string, string>();
  for (const a of areas) colorMap.set(a.label, a.color);

  // Centroid accumulator: label → { sumX, sumY, count }.
  const centroids = new Map<string, { sumX: number; sumY: number; count: number }>();
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const label = areaTiles[r * cols + c];
      if (!label) continue;
      const acc = centroids.get(label);
      if (acc) {
        acc.sumX += c;
        acc.sumY += r;
        acc.count += 1;
      } else {
        centroids.set(label, { sumX: c, sumY: r, count: 1 });
      }
    }
  }

  if (centroids.size === 0) return;

  const fontSize = Math.max(AREA_LABEL_FONT_SIZE_PX * zoom, AREA_LABEL_MIN_FONT_SIZE_PX);

  ctx.save();
  ctx.font = `bold ${fontSize}px 'FS Pixel Sans'`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  for (const [label, acc] of centroids) {
    const cx = offsetX + (acc.sumX / acc.count + 0.5) * s;
    const cy = offsetY + (acc.sumY / acc.count + 0.5) * s;

    // Pixel-art drop shadow (1px right + down, no blur).
    ctx.globalAlpha = AREA_LABEL_SHADOW_ALPHA;
    ctx.fillStyle = AREA_LABEL_SHADOW_COLOR;
    ctx.fillText(label, cx + 1, cy + 1);

    // Main label — area's own color, falling back to white if missing.
    ctx.globalAlpha = AREA_LABEL_ALPHA;
    ctx.fillStyle = colorMap.get(label) ?? AREA_LABEL_FALLBACK_COLOR;
    ctx.fillText(label, cx, cy);
  }
  ctx.restore();
}

/** @internal */
export function renderTileGrid(
  ctx: CanvasRenderingContext2D,
  tileMap: TileTypeVal[][],
  offsetX: number,
  offsetY: number,
  zoom: number,
  tileColors?: Array<ColorValue | null>,
  cols?: number,
  view?: VisibleTiles,
): void {
  const s = TILE_SIZE * zoom;
  const useSpriteFloors = hasFloorSprites();
  const tmRows = tileMap.length;
  const tmCols = tmRows > 0 ? tileMap[0].length : 0;
  const layoutCols = cols ?? tmCols;
  const r0 = view ? Math.max(0, view.r0) : 0;
  const r1 = view ? Math.min(tmRows, view.r1) : tmRows;
  const c0 = view ? Math.max(0, view.c0) : 0;
  const c1 = view ? Math.min(tmCols, view.c1) : tmCols;

  // Floor tiles + wall base color (only the tiles in view)
  for (let r = r0; r < r1; r++) {
    for (let c = c0; c < c1; c++) {
      const tile = tileMap[r][c];

      // Skip VOID tiles entirely (transparent)
      if (tile === TileType.VOID) continue;

      if (tile === TileType.WALL || !useSpriteFloors) {
        // Wall tiles or fallback: solid color
        if (tile === TileType.WALL) {
          const colorIdx = r * layoutCols + c;
          const wallColor = tileColors?.[colorIdx];
          ctx.fillStyle = wallColor ? wallColorToHex(wallColor) : WALL_COLOR;
        } else {
          ctx.fillStyle = FALLBACK_FLOOR_COLOR;
        }
        ctx.fillRect(offsetX + c * s, offsetY + r * s, s, s);
        continue;
      }

      // Floor tile: get colorized sprite
      const colorIdx = r * layoutCols + c;
      const color = tileColors?.[colorIdx] ?? { h: 0, s: 0, b: 0, c: 0 };
      const sprite = getColorizedFloorSprite(tile, color);
      const cached = getCachedSprite(sprite, zoom);
      ctx.drawImage(cached, offsetX + c * s, offsetY + r * s);
    }
  }
}

interface ZDrawable {
  zY: number;
  draw: (ctx: CanvasRenderingContext2D) => void;
}

/** @internal */
export function renderScene(
  ctx: CanvasRenderingContext2D,
  furniture: FurnitureInstance[],
  characters: Character[],
  offsetX: number,
  offsetY: number,
  zoom: number,
  selectedAgentId: number | null,
  hoveredAgentId: number | null,
  pets: Pet[] = [],
  ambient: readonly AmbientResident[] = [],
  trains: readonly Train[] = [],
  halo: { ids: ReadonlySet<number>; intensity: number } | null = null,
): void {
  const drawables: ZDrawable[] = [];

  // Furniture (walls included) — culled to the canvas BEFORE the sprite cache is touched, so
  // off-screen items cost neither a cached canvas nor a sort slot.
  const canvasW = ctx.canvas?.width ?? Infinity;
  const canvasH = ctx.canvas?.height ?? Infinity;
  for (const f of furniture) {
    const fx = offsetX + f.x * zoom;
    const fy = offsetY + f.y * zoom;
    const spriteW = (f.sprite[0]?.length ?? 0) * zoom;
    const spriteH = f.sprite.length * zoom;
    if (fx + spriteW < 0 || fy + spriteH < 0 || fx > canvasW || fy > canvasH) continue;
    const cached = getCachedSprite(f.sprite, zoom);
    if (f.mirrored) {
      drawables.push({
        zY: f.zY,
        draw: (c) => {
          c.save();
          c.translate(fx + cached.width, fy);
          c.scale(-1, 1);
          c.drawImage(cached, 0, 0);
          c.restore();
        },
      });
    } else {
      drawables.push({
        zY: f.zY,
        draw: (c) => {
          c.drawImage(cached, fx, fy);
        },
      });
    }
  }

  // Characters
  for (const ch of characters) {
    const sprites = getCharacterSprites(ch.palette, ch.hueShift);
    const spriteData = getCharacterSprite(ch, sprites);
    const cached = getCachedSprite(spriteData, zoom);
    // Sitting offset: shift character down when seated so they visually sit in the chair
    const sittingOffset = ch.state === CharacterState.TYPE ? CHARACTER_SITTING_OFFSET_PX : 0;
    // Anchor at bottom-center of character — round to integer device pixels
    const drawX = Math.round(offsetX + ch.x * zoom - cached.width / 2);
    const drawY = Math.round(offsetY + (ch.y + sittingOffset) * zoom - cached.height);

    // Sort characters by bottom of their tile (not center) so they render
    // in front of same-row furniture (e.g. chairs) but behind furniture
    // at lower rows (e.g. desks, bookshelves that occlude from below).
    const charZY = ch.y + TILE_SIZE / 2 + CHARACTER_Z_SORT_OFFSET;

    // Headless agents (adopted, no terminal to focus) render translucent while
    // the "Display headless as ghosts" setting is on.
    const alpha = ch.isHeadless && ghostHeadlessAgents ? HEADLESS_CHARACTER_ALPHA : 1;

    // Matrix spawn/despawn effect — skip outline, use per-pixel rendering
    if (ch.matrixEffect) {
      const mDrawX = drawX;
      const mDrawY = drawY;
      const mSpriteData = spriteData;
      const mCh = ch;
      drawables.push({
        zY: charZY,
        draw: (c) => {
          c.save();
          c.globalAlpha = alpha;
          renderMatrixEffect(c, mCh, mSpriteData, mDrawX, mDrawY, zoom);
          c.restore();
        },
      });
      continue;
    }

    // The working halo (office/halo.ts decides; this only draws): a soft AURA hugging this agent's own
    // silhouette, drawn just behind the body: no ring, no floor ellipse, nothing under the feet
    // (getAuraSprite). Its strength is the halo intensity (steady under reduced motion), times the
    // character's own alpha. Real characters only — ambient residents are never in `halo.ids`.
    if (halo && halo.ids.has(ch.id)) {
      const aura = getCachedSprite(getAuraSprite(spriteData), zoom);
      const ax = drawX - HALO_AURA_RADIUS_PX * zoom;
      const ay = drawY - HALO_AURA_RADIUS_PX * zoom;
      const k = halo.intensity * alpha;
      drawables.push({
        zY: charZY - OUTLINE_Z_SORT_OFFSET * 2,
        draw: (c) => {
          c.save();
          c.globalAlpha = k;
          c.drawImage(aura, ax, ay);
          c.restore();
        },
      });
    }

    // White outline: full opacity for selected, 50% for hover
    const isSelected = selectedAgentId !== null && ch.id === selectedAgentId;
    const isHovered = hoveredAgentId !== null && ch.id === hoveredAgentId;
    if (isSelected || isHovered) {
      const outlineAlpha = isSelected ? SELECTED_OUTLINE_ALPHA : HOVERED_OUTLINE_ALPHA;
      const outlineData = getOutlineSprite(spriteData);
      const outlineCached = getCachedSprite(outlineData, zoom);
      const olDrawX = drawX - zoom; // 1 sprite-pixel offset, scaled
      const olDrawY = drawY - zoom; // outline follows sitting offset via drawY
      drawables.push({
        zY: charZY - OUTLINE_Z_SORT_OFFSET, // sort just before character
        draw: (c) => {
          c.save();
          c.globalAlpha = outlineAlpha;
          c.drawImage(outlineCached, olDrawX, olDrawY);
          c.restore();
        },
      });
    }

    drawables.push({
      zY: charZY,
      draw: (c) => {
        if (alpha === 1) {
          c.drawImage(cached, drawX, drawY);
          return;
        }
        c.save();
        c.globalAlpha = alpha;
        c.drawImage(cached, drawX, drawY);
        c.restore();
      },
    });
  }

  // ── Pets ──────────────────────────────────────────────
  for (const pet of pets) {
    const petSprites = getPetSprites(pet.petType);
    const spriteData = getPetSpriteData(pet, petSprites);
    if (!spriteData) continue;

    const cached = getCachedSprite(spriteData, zoom);
    // Anchor at bottom-center at (pet.x, pet.y) — round to integer device pixels
    const drawX = Math.round(offsetX + pet.x * zoom - cached.width / 2);
    const drawY = Math.round(offsetY + pet.y * zoom - cached.height);

    // Z-sort key: matches the chair/character "row boundary" formula.
    // pet.y is the pixel center, so + TILE_SIZE/2 lifts us to the row's bottom edge.
    const petZY = pet.y + TILE_SIZE / 2;

    drawables.push({
      zY: petZY,
      draw: (c) => {
        c.drawImage(cached, drawX, drawY);
      },
    });
  }

  // ── Ambient residents (decorative; never agents) ───────
  // Character art with a job badge; culled to the canvas before the sprite cache is touched.
  const canvasWidthPx = ctx.canvas?.width ?? Infinity;
  const canvasHeightPx = ctx.canvas?.height ?? Infinity;
  for (const r of ambient) {
    if (r.state === 'riding') continue; // aboard a train: drawn as the train
    const sx = offsetX + r.x * zoom;
    const sy = offsetY + r.y * zoom;
    if (sx < -TILE_SIZE * zoom || sy < -2 * TILE_SIZE * zoom) continue;
    if (sx > canvasWidthPx + TILE_SIZE * zoom || sy > canvasHeightPx + 2 * TILE_SIZE * zoom) continue;
    // a job with a delivered sheet draws from it (ambientSprites.ts); others keep the palette sprite
    const sheet = ambientSheetFor(r.job);
    const cached = getCachedSprite(
      sheet
        ? ambientFrame(sheet, r.dir, r.state === 'walk', r.frame)
        : getCharacterSprites(r.palette, r.hueShift).walk[r.dir][r.state === 'walk' ? r.frame % 4 : 1],
      zoom,
    );
    const drawX = Math.round(sx - cached.width / 2);
    const drawY = Math.round(sy - cached.height);
    const badge = AMBIENT_JOB_COLORS[r.job];
    const bx = Math.round(sx - (AMBIENT_BADGE_W / 2) * zoom);
    const by = Math.round(offsetY + (r.y - AMBIENT_BADGE_OFFSET_Y) * zoom);
    drawables.push({
      zY: r.y + TILE_SIZE / 2 + CHARACTER_Z_SORT_OFFSET,
      draw: (c) => {
        c.drawImage(cached, drawX, drawY);
        if (badge) {
          c.fillStyle = badge;
          c.fillRect(bx, by, AMBIENT_BADGE_W * zoom, AMBIENT_BADGE_H * zoom);
        }
      },
    });
  }

  // ── Subway trains (decorative; cabs at both ends, passenger-car frames A/B while moving) ──
  for (const tr of trains) {
    const cars = TRAIN_CARS.map((car) => {
      const type = car.frameB && tr.state === 'run' && tr.frame === 1 ? car.frameB : car.type;
      return getCatalogEntry(type)?.sprite ?? null;
    });
    if (cars.some((c) => !c)) continue;
    const total = cars.reduce((n, c) => n + (c ? c[0].length : 0), 0);
    let left = tr.x * TILE_SIZE - total / 2;
    for (const sprite of cars) {
      if (!sprite) continue;
      const w = sprite[0].length;
      const h = sprite.length;
      const dx = Math.round(offsetX + left * zoom);
      const dy = Math.round(offsetY + (tr.railY - h + 2) * zoom);
      left += w;
      if (dx > canvasWidthPx || dx + w * zoom < 0 || dy > canvasHeightPx || dy + h * zoom < 0) continue;
      const cached = getCachedSprite(sprite, zoom);
      drawables.push({ zY: tr.railY + 4, draw: (c) => c.drawImage(cached, dx, dy) });
    }
  }

  // Sort by Y (lower = in front = drawn later)
  drawables.sort((a, b) => a.zY - b.zY);

  for (const d of drawables) {
    d.draw(ctx);
  }
}

function renderAmbientLabel(
  ctx: CanvasRenderingContext2D,
  r: AmbientResident,
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  const text = ambientLabel(r.job);
  const fontPx = Math.max(AMBIENT_LABEL_FONT_PX, Math.round(AMBIENT_LABEL_FONT_PX * zoom * 0.5));
  ctx.save();
  ctx.font = `${fontPx}px "FS Pixel Sans", monospace`;
  const w = Math.ceil(ctx.measureText(text).width) + fontPx;
  const h = fontPx + Math.round(fontPx * 0.6);
  const x = Math.round(offsetX + r.x * zoom - w / 2);
  const y = Math.round(offsetY + (r.y - AMBIENT_BADGE_OFFSET_Y - 4) * zoom - h);
  ctx.fillStyle = AMBIENT_LABEL_BG;
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = AMBIENT_LABEL_FG;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x + Math.round(fontPx / 2), y + Math.round(h / 2));
  ctx.restore();
}

// ── Seat indicators ─────────────────────────────────────────────

function renderSeatIndicators(
  ctx: CanvasRenderingContext2D,
  seats: Map<string, Seat>,
  characters: Map<number, Character>,
  selectedAgentId: number | null,
  hoveredTile: { col: number; row: number } | null,
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  if (selectedAgentId === null || !hoveredTile) return;
  const selectedChar = characters.get(selectedAgentId);
  if (!selectedChar) return;

  // Only show indicator for the hovered seat tile
  for (const [uid, seat] of seats) {
    if (seat.seatCol !== hoveredTile.col || seat.seatRow !== hoveredTile.row) continue;

    const s = TILE_SIZE * zoom;
    const x = offsetX + seat.seatCol * s;
    const y = offsetY + seat.seatRow * s;

    if (selectedChar.seatId === uid) {
      // Selected agent's own seat — blue
      ctx.fillStyle = SEAT_OWN_COLOR;
    } else if (!seat.assigned) {
      // Available seat — green
      ctx.fillStyle = SEAT_AVAILABLE_COLOR;
    } else {
      // Busy (assigned to another agent) — red
      ctx.fillStyle = SEAT_BUSY_COLOR;
    }
    ctx.fillRect(x, y, s, s);
    break;
  }
}

// ── Edit mode overlays ──────────────────────────────────────────

/** @internal */
export function renderGridOverlay(
  ctx: CanvasRenderingContext2D,
  offsetX: number,
  offsetY: number,
  zoom: number,
  cols: number,
  rows: number,
  tileMap?: TileTypeVal[][],
): void {
  const s = TILE_SIZE * zoom;
  ctx.strokeStyle = GRID_LINE_COLOR;
  ctx.lineWidth = 1;
  ctx.beginPath();
  // Vertical lines — offset by 0.5 for crisp 1px lines
  for (let c = 0; c <= cols; c++) {
    const x = offsetX + c * s + 0.5;
    ctx.moveTo(x, offsetY);
    ctx.lineTo(x, offsetY + rows * s);
  }
  // Horizontal lines
  for (let r = 0; r <= rows; r++) {
    const y = offsetY + r * s + 0.5;
    ctx.moveTo(offsetX, y);
    ctx.lineTo(offsetX + cols * s, y);
  }
  ctx.stroke();

  // Draw faint dashed outlines on VOID tiles
  if (tileMap) {
    ctx.save();
    ctx.strokeStyle = VOID_TILE_OUTLINE_COLOR;
    ctx.lineWidth = 1;
    ctx.setLineDash(VOID_TILE_DASH_PATTERN);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (tileMap[r]?.[c] === TileType.VOID) {
          ctx.strokeRect(offsetX + c * s + 0.5, offsetY + r * s + 0.5, s - 1, s - 1);
        }
      }
    }
    ctx.restore();
  }
}

/** Draw faint expansion placeholders 1 tile outside grid bounds (ghost border). */
function renderGhostBorder(
  ctx: CanvasRenderingContext2D,
  offsetX: number,
  offsetY: number,
  zoom: number,
  cols: number,
  rows: number,
  ghostHoverCol: number,
  ghostHoverRow: number,
): void {
  const s = TILE_SIZE * zoom;
  ctx.save();

  // Collect ghost border tiles: one ring around the grid
  const ghostTiles: Array<{ c: number; r: number }> = [];
  // Top and bottom rows
  for (let c = -1; c <= cols; c++) {
    ghostTiles.push({ c, r: -1 });
    ghostTiles.push({ c, r: rows });
  }
  // Left and right columns (excluding corners already added)
  for (let r = 0; r < rows; r++) {
    ghostTiles.push({ c: -1, r });
    ghostTiles.push({ c: cols, r });
  }

  for (const { c, r } of ghostTiles) {
    const x = offsetX + c * s;
    const y = offsetY + r * s;
    const isHovered = c === ghostHoverCol && r === ghostHoverRow;
    if (isHovered) {
      ctx.fillStyle = GHOST_BORDER_HOVER_FILL;
      ctx.fillRect(x, y, s, s);
    }
    ctx.strokeStyle = isHovered ? GHOST_BORDER_HOVER_STROKE : GHOST_BORDER_STROKE;
    ctx.lineWidth = 1;
    ctx.setLineDash(VOID_TILE_DASH_PATTERN);
    ctx.strokeRect(x + 0.5, y + 0.5, s - 1, s - 1);
  }

  ctx.restore();
}

/** @internal */
export function renderGhostPreview(
  ctx: CanvasRenderingContext2D,
  sprite: SpriteData,
  col: number,
  row: number,
  valid: boolean,
  offsetX: number,
  offsetY: number,
  zoom: number,
  mirrored: boolean = false,
): void {
  const cached = getCachedSprite(sprite, zoom);
  const x = offsetX + col * TILE_SIZE * zoom;
  const y = offsetY + row * TILE_SIZE * zoom;
  ctx.save();
  ctx.globalAlpha = GHOST_PREVIEW_SPRITE_ALPHA;
  if (mirrored) {
    ctx.translate(x + cached.width, y);
    ctx.scale(-1, 1);
    ctx.drawImage(cached, 0, 0);
  } else {
    ctx.drawImage(cached, x, y);
  }
  // Tint overlay — reset transform for correct fill position
  ctx.restore();
  ctx.save();
  ctx.globalAlpha = GHOST_PREVIEW_TINT_ALPHA;
  ctx.fillStyle = valid ? GHOST_VALID_TINT : GHOST_INVALID_TINT;
  ctx.fillRect(x, y, cached.width, cached.height);
  ctx.restore();
}

/** @internal */
export function renderSelectionHighlight(
  ctx: CanvasRenderingContext2D,
  col: number,
  row: number,
  w: number,
  h: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  const s = TILE_SIZE * zoom;
  const x = offsetX + col * s;
  const y = offsetY + row * s;
  ctx.save();
  ctx.strokeStyle = SELECTION_HIGHLIGHT_COLOR;
  ctx.lineWidth = 2;
  ctx.setLineDash(SELECTION_DASH_PATTERN);
  ctx.strokeRect(x + 1, y + 1, w * s - 2, h * s - 2);
  ctx.restore();
}

/** @internal */
export function renderDeleteButton(
  ctx: CanvasRenderingContext2D,
  col: number,
  row: number,
  w: number,
  _h: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
): DeleteButtonBounds {
  const s = TILE_SIZE * zoom;
  // Position at top-right corner of selected furniture
  const cx = offsetX + (col + w) * s + 1;
  const cy = offsetY + row * s - 1;
  const radius = Math.max(BUTTON_MIN_RADIUS, zoom * BUTTON_RADIUS_ZOOM_FACTOR);

  // Circle background
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = DELETE_BUTTON_BG;
  ctx.fill();

  // X mark
  ctx.strokeStyle = BUTTON_ICON_COLOR;
  ctx.lineWidth = Math.max(BUTTON_LINE_WIDTH_MIN, zoom * BUTTON_LINE_WIDTH_ZOOM_FACTOR);
  ctx.lineCap = 'round';
  const xSize = radius * BUTTON_ICON_SIZE_FACTOR;
  ctx.beginPath();
  ctx.moveTo(cx - xSize, cy - xSize);
  ctx.lineTo(cx + xSize, cy + xSize);
  ctx.moveTo(cx + xSize, cy - xSize);
  ctx.lineTo(cx - xSize, cy + xSize);
  ctx.stroke();
  ctx.restore();

  return { cx, cy, radius };
}

function renderRotateButton(
  ctx: CanvasRenderingContext2D,
  col: number,
  row: number,
  _w: number,
  _h: number,
  offsetX: number,
  offsetY: number,
  zoom: number,
): RotateButtonBounds {
  const s = TILE_SIZE * zoom;
  // Position to the left of the delete button (which is at top-right corner)
  const radius = Math.max(BUTTON_MIN_RADIUS, zoom * BUTTON_RADIUS_ZOOM_FACTOR);
  const cx = offsetX + col * s - 1;
  const cy = offsetY + row * s - 1;

  // Circle background
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = ROTATE_BUTTON_BG;
  ctx.fill();

  // Circular arrow icon
  ctx.strokeStyle = BUTTON_ICON_COLOR;
  ctx.lineWidth = Math.max(BUTTON_LINE_WIDTH_MIN, zoom * BUTTON_LINE_WIDTH_ZOOM_FACTOR);
  ctx.lineCap = 'round';
  const arcR = radius * BUTTON_ICON_SIZE_FACTOR;
  ctx.beginPath();
  // Draw a 270-degree arc
  ctx.arc(cx, cy, arcR, -Math.PI * 0.8, Math.PI * 0.7);
  ctx.stroke();
  // Draw arrowhead at the end of the arc
  const endAngle = Math.PI * 0.7;
  const endX = cx + arcR * Math.cos(endAngle);
  const endY = cy + arcR * Math.sin(endAngle);
  const arrowSize = radius * 0.35;
  ctx.beginPath();
  ctx.moveTo(endX + arrowSize * 0.6, endY - arrowSize * 0.3);
  ctx.lineTo(endX, endY);
  ctx.lineTo(endX + arrowSize * 0.7, endY + arrowSize * 0.5);
  ctx.stroke();
  ctx.restore();

  return { cx, cy, radius };
}

// ── Speech bubbles ──────────────────────────────────────────────

function renderBubbles(
  ctx: CanvasRenderingContext2D,
  characters: Character[],
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  for (const ch of characters) {
    if (!ch.bubbleType) continue;
    // The green checkmark bubble only represents "done" (turn finished). The
    // idle "Waiting for input" state communicates via its overlay label, not a
    // bubble, so skip the bubble for it.
    if (ch.bubbleType === 'waiting' && ch.waitingAwaitingInput) continue;

    const sprite =
      ch.bubbleType === 'permission' ? BUBBLE_PERMISSION_SPRITE : BUBBLE_WAITING_SPRITE;

    // Compute opacity: permission = full, waiting = fade in last 0.5s
    let alpha = 1.0;
    if (ch.bubbleType === 'waiting' && ch.bubbleTimer < BUBBLE_FADE_DURATION_SEC) {
      alpha = ch.bubbleTimer / BUBBLE_FADE_DURATION_SEC;
    }

    const cached = getCachedSprite(sprite, zoom);
    // Position: centered above the character's head
    // Character is anchored bottom-center at (ch.x, ch.y), sprite is 16x24
    // Place bubble above head with a small gap; follow sitting offset
    const sittingOff = ch.state === CharacterState.TYPE ? BUBBLE_SITTING_OFFSET_PX : 0;
    const bubbleX = Math.round(offsetX + ch.x * zoom - cached.width / 2);
    const bubbleY = Math.round(
      offsetY + (ch.y + sittingOff - BUBBLE_VERTICAL_OFFSET_PX) * zoom - cached.height - 1 * zoom,
    );

    ctx.save();
    if (alpha < 1.0) ctx.globalAlpha = alpha;
    ctx.drawImage(cached, bubbleX, bubbleY);
    ctx.restore();
  }
}

function renderPetBubbles(
  ctx: CanvasRenderingContext2D,
  pets: Pet[],
  offsetX: number,
  offsetY: number,
  zoom: number,
): void {
  for (const pet of pets) {
    if (!pet.bubbleType) continue;

    const sprite = BUBBLE_HEART_SPRITE;

    // Fade in the last BUBBLE_FADE_DURATION_SEC of the lifetime
    let alpha = 1.0;
    if (pet.bubbleTimer < BUBBLE_FADE_DURATION_SEC) {
      alpha = Math.max(0, pet.bubbleTimer / BUBBLE_FADE_DURATION_SEC);
    }

    const cached = getCachedSprite(sprite, zoom);
    // Anchor: centered above the pet's head. Pet is anchored bottom-center at
    // (pet.x, pet.y); sprite is ~16 tall, so back up TILE_SIZE pixels and add
    // a 1-sprite-pixel gap (scaled by zoom).
    const bubbleX = Math.round(offsetX + pet.x * zoom - cached.width / 2);
    const bubbleY = Math.round(offsetY + (pet.y - TILE_SIZE) * zoom - cached.height - 1 * zoom);

    ctx.save();
    if (alpha < 1.0) ctx.globalAlpha = alpha;
    ctx.drawImage(cached, bubbleX, bubbleY);
    ctx.restore();
  }
}

export interface ButtonBounds {
  /** Center X in device pixels */
  cx: number;
  /** Center Y in device pixels */
  cy: number;
  /** Radius in device pixels */
  radius: number;
}

export type DeleteButtonBounds = ButtonBounds;
export type RotateButtonBounds = ButtonBounds;

export interface EditorRenderState {
  showGrid: boolean;
  ghostSprite: SpriteData | null;
  ghostMirrored: boolean;
  ghostCol: number;
  ghostRow: number;
  ghostValid: boolean;
  selectedCol: number;
  selectedRow: number;
  selectedW: number;
  selectedH: number;
  hasSelection: boolean;
  isRotatable: boolean;
  /** Updated each frame by renderDeleteButton */
  deleteButtonBounds: DeleteButtonBounds | null;
  /** Updated each frame by renderRotateButton */
  rotateButtonBounds: RotateButtonBounds | null;
  /** Whether to show ghost border (expansion tiles outside grid) */
  showGhostBorder: boolean;
  /** Hovered ghost border tile col (-1 to cols) */
  ghostBorderHoverCol: number;
  /** Hovered ghost border tile row (-1 to rows) */
  ghostBorderHoverRow: number;
}

export interface SelectionRenderState {
  selectedAgentId: number | null;
  hoveredAgentId: number | null;
  hoveredTile: { col: number; row: number } | null;
  seats: Map<string, Seat>;
  characters: Map<number, Character>;
}

export function renderFrame(
  ctx: CanvasRenderingContext2D,
  canvasWidth: number,
  canvasHeight: number,
  tileMap: TileTypeVal[][],
  furniture: FurnitureInstance[],
  characters: Character[],
  zoom: number,
  panX: number,
  panY: number,
  selection?: SelectionRenderState,
  editor?: EditorRenderState,
  tileColors?: Array<ColorValue | null>,
  layoutCols?: number,
  layoutRows?: number,
  carpetTiles?: Array<CarpetTile | null>,
  areas?: AreaDefinition[],
  areaTiles?: Array<string | null>,
  showAreas?: boolean,
  activeAreaLabel?: string | null,
  pets?: Pet[],
  ambient?: readonly AmbientResident[],
  hoveredAmbientId?: string | null,
  trains?: readonly Train[],
  halo?: { ids: ReadonlySet<number>; intensity: number } | null,
): { offsetX: number; offsetY: number } {
  // Clear
  ctx.clearRect(0, 0, canvasWidth, canvasHeight);

  // Use layout dimensions (fallback to tileMap size)
  const cols = layoutCols ?? (tileMap.length > 0 ? tileMap[0].length : 0);
  const rows = layoutRows ?? tileMap.length;

  // Center map in viewport + pan offset (integer device pixels). Shared with
  // the DOM overlays so a label lands exactly on the sprite it belongs to.
  const { offsetX, offsetY } = mapOffset(canvasWidth, canvasHeight, cols, rows, zoom, panX, panY);
  const view = visibleTiles(canvasWidth, canvasHeight, offsetX, offsetY, zoom, cols, rows);

  // Draw tiles (floor + wall base color)
  renderTileGrid(ctx, tileMap, offsetX, offsetY, zoom, tileColors, layoutCols, view);

  // Carpet layer (above floor, below seat indicators / furniture / characters)
  if (carpetTiles && carpetTiles.length > 0) {
    renderCarpetLayer(ctx, carpetTiles, cols, rows, offsetX, offsetY, zoom, view);
  }

  // Area overlay (translucent color wash) — above carpets, below seat indicators
  if (showAreas) {
    renderAreaOverlay(
      ctx,
      areaTiles,
      areas,
      cols,
      rows,
      offsetX,
      offsetY,
      zoom,
      activeAreaLabel,
      view,
    );
  }

  // Seat indicators (below furniture/characters, on top of floor)
  if (selection) {
    renderSeatIndicators(
      ctx,
      selection.seats,
      selection.characters,
      selection.selectedAgentId,
      selection.hoveredTile,
      offsetX,
      offsetY,
      zoom,
    );
  }

  // Build wall instances for z-sorting with furniture and characters
  const wallInstances = hasWallSprites() ? getWallInstances(tileMap, tileColors, layoutCols) : [];
  const allFurniture = wallInstances.length > 0 ? [...wallInstances, ...furniture] : furniture;

  // Draw walls + furniture + characters (z-sorted)
  const selectedId = selection?.selectedAgentId ?? null;
  const hoveredId = selection?.hoveredAgentId ?? null;
  renderScene(
    ctx,
    allFurniture,
    characters,
    offsetX,
    offsetY,
    zoom,
    selectedId,
    hoveredId,
    pets ?? [],
    ambient ?? [],
    trains ?? [],
    halo ?? null,
  );

  // Speech bubbles (always on top of characters)
  renderBubbles(ctx, characters, offsetX, offsetY, zoom);
  // Pet heart bubbles (same overlay pass)
  if (pets && pets.length > 0) {
    renderPetBubbles(ctx, pets, offsetX, offsetY, zoom);
  }
  // Hovered ambient resident: its label always says Ambient and decorative.
  if (hoveredAmbientId && ambient) {
    const r = ambient.find((a) => a.id === hoveredAmbientId);
    if (r) renderAmbientLabel(ctx, r, offsetX, offsetY, zoom);
  }

  // Area labels (above bubbles + characters, below editor overlays)
  if (showAreas) {
    renderAreaLabels(ctx, areaTiles, areas, cols, rows, offsetX, offsetY, zoom);
  }

  // Editor overlays
  if (editor) {
    if (editor.showGrid) {
      renderGridOverlay(ctx, offsetX, offsetY, zoom, cols, rows, tileMap);
    }
    if (editor.showGhostBorder) {
      renderGhostBorder(
        ctx,
        offsetX,
        offsetY,
        zoom,
        cols,
        rows,
        editor.ghostBorderHoverCol,
        editor.ghostBorderHoverRow,
      );
    }
    if (editor.ghostSprite && editor.ghostCol >= 0) {
      renderGhostPreview(
        ctx,
        editor.ghostSprite,
        editor.ghostCol,
        editor.ghostRow,
        editor.ghostValid,
        offsetX,
        offsetY,
        zoom,
        editor.ghostMirrored,
      );
    }
    if (editor.hasSelection) {
      renderSelectionHighlight(
        ctx,
        editor.selectedCol,
        editor.selectedRow,
        editor.selectedW,
        editor.selectedH,
        offsetX,
        offsetY,
        zoom,
      );
      editor.deleteButtonBounds = renderDeleteButton(
        ctx,
        editor.selectedCol,
        editor.selectedRow,
        editor.selectedW,
        editor.selectedH,
        offsetX,
        offsetY,
        zoom,
      );
      if (editor.isRotatable) {
        editor.rotateButtonBounds = renderRotateButton(
          ctx,
          editor.selectedCol,
          editor.selectedRow,
          editor.selectedW,
          editor.selectedH,
          offsetX,
          offsetY,
          zoom,
        );
      } else {
        editor.rotateButtonBounds = null;
      }
    } else {
      editor.deleteButtonBounds = null;
      editor.rotateButtonBounds = null;
    }
  }

  return { offsetX, offsetY };
}
