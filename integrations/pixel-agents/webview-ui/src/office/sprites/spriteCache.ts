import { HALO_AURA_ALPHAS, HALO_AURA_CACHE_MAX_ENTRIES, HALO_AURA_RADIUS_PX, HALO_AURA_RGB } from '../../constants.js';
import { registerRenderCache } from '../renderCaches.js';
import type { SpriteData } from '../types.js';

const zoomCaches = new Map<number, WeakMap<SpriteData, HTMLCanvasElement>>();

/** The render-cache lifecycle (renderCaches.ts): forget every cached bitmap, so the next draw rebuilds it. The canvases key on the
 *  sprite data, which a colorize reset does not replace (characters, uncolored furniture), so they need their own clear. The outline
 *  and aura caches below hold sprite DATA, not bitmaps: their canvases are among these. */
export function clearSpriteCaches(): void {
  zoomCaches.clear();
}

// joins the lifecycle (renderCaches.ts does not import this DOM module); it holds no carpet-layer pixels, so it reports none
registerRenderCache({ reset: clearSpriteCaches, pixels: () => 0 });

// ── Outline sprite generation ─────────────────────────────────

const outlineCache = new WeakMap<SpriteData, SpriteData>();
/** Aura masks, least-recently-used first (Map insertion order); HARD cap HALO_AURA_CACHE_MAX_ENTRIES. */
const auraCache = new Map<SpriteData, SpriteData>();

/** @internal The aura-mask cache's current entry count (for its cap test). */
export function auraCacheSize(): number {
  return auraCache.size;
}

/** Generate a 1px white outline SpriteData (2px larger in each dimension) */
export function getOutlineSprite(sprite: SpriteData): SpriteData {
  const cached = outlineCache.get(sprite);
  if (cached) return cached;

  const rows = sprite.length;
  const cols = sprite[0].length;
  // Expanded grid: +2 in each dimension for 1px border
  const outline: string[][] = [];
  for (let r = 0; r < rows + 2; r++) {
    outline.push(new Array<string>(cols + 2).fill(''));
  }

  // For each opaque pixel, mark its 4 cardinal neighbors as white
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (sprite[r][c] === '') continue;
      const er = r + 1;
      const ec = c + 1;
      if (outline[er - 1][ec] === '') outline[er - 1][ec] = '#FFFFFF';
      if (outline[er + 1][ec] === '') outline[er + 1][ec] = '#FFFFFF';
      if (outline[er][ec - 1] === '') outline[er][ec - 1] = '#FFFFFF';
      if (outline[er][ec + 1] === '') outline[er][ec + 1] = '#FFFFFF';
    }
  }

  // Clear pixels that overlap with original opaque pixels
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (sprite[r][c] !== '') {
        outline[r + 1][c + 1] = '';
      }
    }
  }

  outlineCache.set(sprite, outline);
  return outline;
}

/**
 * The working-halo AURA for a sprite: transparent pixels within HALO_AURA_RADIUS_PX (Euclidean) of the
 * silhouette, in HALO_AURA_RGB, fading by distance band (HALO_AURA_ALPHAS). The grid is the sprite
 * expanded by the radius on every side. Never on the sprite's own pixels, and never BELOW its lowest
 * opaque row, so nothing is drawn on the floor under the feet. Cached under a HARD cap with
 * least-recently-used eviction; an evicted mask is rebuilt on its next use.
 */
export function getAuraSprite(sprite: SpriteData): SpriteData {
  const cached = auraCache.get(sprite);
  if (cached) {
    auraCache.delete(sprite);
    auraCache.set(sprite, cached);
    return cached;
  }
  const R = HALO_AURA_RADIUS_PX;
  const rows = sprite.length;
  const cols = sprite[0].length;
  let lowest = -1;
  for (let r = 0; r < rows; r++) if (sprite[r].some((p) => p !== '')) lowest = r;
  const aura: string[][] = [];
  for (let r = 0; r < rows + 2 * R; r++) aura.push(new Array<string>(cols + 2 * R).fill(''));
  const alphaHex = HALO_AURA_ALPHAS.map((a) => a.toString(16).padStart(2, '0'));
  for (let er = 0; er < rows + 2 * R && er - R <= lowest; er++) {
    for (let ec = 0; ec < cols + 2 * R; ec++) {
      const sr = er - R;
      const sc = ec - R;
      if (sr >= 0 && sr < rows && sc >= 0 && sc < cols && sprite[sr][sc] !== '') continue;
      let best = Infinity;
      for (let dr = -R; dr <= R; dr++) {
        for (let dc = -R; dc <= R; dc++) {
          const r = sr + dr;
          const c = sc + dc;
          if (r < 0 || r >= rows || c < 0 || c >= cols || sprite[r][c] === '') continue;
          best = Math.min(best, Math.hypot(dr, dc));
        }
      }
      const band = Math.ceil(best - 1e-9);
      if (band >= 1 && band <= R) aura[er][ec] = HALO_AURA_RGB + alphaHex[band - 1];
    }
  }
  auraCache.set(sprite, aura);
  if (auraCache.size > HALO_AURA_CACHE_MAX_ENTRIES) auraCache.delete(auraCache.keys().next().value as SpriteData);
  return aura;
}

export function getCachedSprite(sprite: SpriteData, zoom: number): HTMLCanvasElement {
  let cache = zoomCaches.get(zoom);
  if (!cache) {
    cache = new WeakMap();
    zoomCaches.set(zoom, cache);
  }

  const cached = cache.get(sprite);
  if (cached) return cached;

  const rows = sprite.length;
  const cols = sprite[0].length;
  const canvas = document.createElement('canvas');
  canvas.width = cols * zoom;
  canvas.height = rows * zoom;
  const ctx = canvas.getContext('2d')!;
  ctx.imageSmoothingEnabled = false;

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const color = sprite[r][c];
      if (color === '') continue;
      ctx.fillStyle = color;
      ctx.fillRect(c * zoom, r * zoom, zoom, zoom);
    }
  }

  cache.set(sprite, canvas);
  return canvas;
}
