/**
 * RENDER-CACHE LIFECYCLE — the per-layout caps (core/src/layoutLimits.ts) bound what ONE layout may
 * hold; this bounds what the PROCESS keeps across successive loads, imports and edits. The strong
 * caches of derived sprites are:
 *   - colorize.ts colorizeCache — colorized / adjusted floors, walls, furniture variants;
 *   - carpetTiles.ts carpetCache — dual-colorized carpet junction sprites;
 *   - engine/carpetPlan.ts the carpet junction plan, and renderer.ts the pre-composed carpet layer
 *     (one entry each; the renderer REGISTERS its layer here, so this module stays DOM-free).
 * spriteCache.ts keeps a canvas per zoom in WeakMaps KEYED by those sprites, so its canvases are
 * released with them: bounding the strong caches is what bounds the canvases. It registers a clear of
 * its own (clearSpriteCaches), as does city/cityRender.ts for its two light sprites; neither reports
 * pixels, so renderCacheStats().carpetLayerPixels stays the carpet layer's.
 *
 * LIFECYCLE (both are clears at a safe point between frames, never mid-frame; whatever the next
 * frame needs is recomputed lazily on first use):
 *   - resetRenderCaches(): on every layout LOAD or REPLACEMENT — layoutLoaded (the handshake's
 *     file read, a refused save's re-send, the VS Code import) and the browser Import (App's
 *     applyImport rebuild) — so one layout's palettes never accumulate into the next's;
 *   - boundRenderCaches(): on every layout REBUILD (editor edits, undo/redo, DI reshapes), a clear
 *     only when the caches together exceed RENDER_CACHE_MAX_ENTRIES — so edits that keep bringing
 *     NEW palettes cannot grow them without bound, while ordinary edits keep their warm caches;
 *   - a canvas context that is lost and RESTORED comes back EMPTY, and every bitmap drawn once and kept
 *     (the sprite canvases, the carpet layer, the city's light sprites) is blank with it. The supported
 *     condition, exactly: a `contextlost` or `contextrestored` event on the office canvas
 *     (engine/gameLoop.ts) or on the carpet layer's canvas (engine/renderer.ts) calls resetRenderCaches().
 *     A loss that reaches neither (the small cached canvases carry no listeners of their own) is not
 *     detected, nor is one on the World Map's own canvases;
 *   - EDITOR PREVIEWS (brush swatches for floor, wall and carpet, catalogue thumbnails, the
 *     placement ghost) never enter the layout caches: they use the PREVIEW caches
 *     (colorize.getPreviewColorizedSprite, carpetTiles.getCarpetPreviewJunctionSprite), bounded AT
 *     INSERTION, because a brush-colour change rebuilds no layout (review round 1, F1).
 */
import { RENDER_CACHE_MAX_ENTRIES } from '../constants.js';
import {
  clearColorizeCache,
  clearPreviewCache,
  colorizeCacheSize,
  previewCacheSize,
} from './colorize.js';
import { resetCarpetPlan } from './engine/carpetPlan.js';
import {
  carpetCacheSize,
  carpetPreviewCacheSize,
  clearCarpetCache,
} from './sprites/carpetTiles.js';

let resets = 0;
/** Caches owned by DOM modules (the renderer's carpet layer), registered at their module load. */
const registered: Array<{ reset: () => void; pixels: () => number }> = [];
export function registerRenderCache(cache: { reset: () => void; pixels: () => number }): void {
  registered.push(cache);
}

export function resetRenderCaches(): void {
  clearColorizeCache();
  clearPreviewCache();
  clearCarpetCache(); // and the carpet preview cache
  resetCarpetPlan();
  for (const c of registered) c.reset();
  resets++;
}

/** Clear when over the bound; returns whether it cleared. */
export function boundRenderCaches(): boolean {
  if (colorizeCacheSize() + carpetCacheSize() <= RENDER_CACHE_MAX_ENTRIES) return false;
  resetRenderCaches();
  return true;
}

/** Render-cache accounting (e2e evidence). */
export function renderCacheStats(): {
  colorizeEntries: number;
  carpetEntries: number;
  previewEntries: number;
  carpetPreviewEntries: number;
  carpetLayerPixels: number;
  resets: number;
} {
  return {
    colorizeEntries: colorizeCacheSize(),
    carpetEntries: carpetCacheSize(),
    previewEntries: previewCacheSize(),
    carpetPreviewEntries: carpetPreviewCacheSize(),
    carpetLayerPixels: registered.reduce((n, c) => n + c.pixels(), 0),
    resets,
  };
}
