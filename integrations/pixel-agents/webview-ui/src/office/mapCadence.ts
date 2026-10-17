/**
 * THE MAP'S ONE TICKER (M3 S7): one immediate draw, then one draw per WORLD_MAP_REFRESH_MS
 * (WORLD_MAP_REFRESH_REDUCED_MS under reduced motion), until stopped. The living layer, the halo and
 * the characters all draw inside this tick, so this cadence is the Map's whole redraw rate.
 * DOM-free (timers are injectable), so the cadence is tested as is.
 */
import { WORLD_MAP_REFRESH_MS, WORLD_MAP_REFRESH_REDUCED_MS } from '../constants.js';

export interface MapTimers {
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (id: unknown) => void;
}

export function mapRefreshMs(reducedMotion: boolean): number {
  return reducedMotion ? WORLD_MAP_REFRESH_REDUCED_MS : WORLD_MAP_REFRESH_MS;
}

/** Start the ticker; returns the function that stops it. */
export function startMapTicker(tick: () => void, reducedMotion: boolean, timers: MapTimers = globalThis as unknown as MapTimers): () => void {
  tick();
  const id = timers.setInterval(tick, mapRefreshMs(reducedMotion));
  return () => timers.clearInterval(id);
}
