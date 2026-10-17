/**
 * THE MAP'S LIVING LAYER (M3 S3, C3): ambient residents and moving trains drawn as simple markers on
 * the full-world Map, from the SAME live objects the canvas draws (OfficeState's resident list and
 * the transit trains) at the Map's own bounded cadence (WORLD_MAP_REFRESH_MS). Markers only:
 * residents are job-coloured dots and trains teal bars. Riders aboard a train are part of the
 * train's bar. Real agents and pets are drawn AFTER this layer by the Map, so they always sit on top
 * (real-agent priority). Nothing here can style a resident as an agent: no halo, no status, no name.
 */
import {
  AMBIENT_JOB_COLORS,
  TRANSIT_MAP_TRAIN_COLOR,
  WORLD_MAP_RESIDENT_DOT_PX,
  WORLD_MAP_TRAIN_BAR_PX,
} from '../constants.js';
import type { AmbientResident } from './ambient/ambientWorld.js';
import type { Train } from './transit/transitWorld.js';
import { TRAIN_LENGTH_TILES } from './transit/transitWorld.js';
import { TILE_SIZE } from './types.js';

/** The two canvas operations the markers need (a 2D context satisfies it). */
export interface MarkerCanvas {
  fillStyle: unknown;
  fillRect(x: number, y: number, w: number, h: number): void;
}

/** Draw residents and trains in CANVAS pixels; sx/sy map world pixels to canvas pixels. */
export function drawLivingLayer(
  ctx: MarkerCanvas,
  residents: readonly AmbientResident[],
  trains: readonly Train[],
  sx: number,
  sy: number,
): { residents: number; trains: number } {
  let drawnResidents = 0;
  const d = WORLD_MAP_RESIDENT_DOT_PX;
  for (const r of residents) {
    if (r.state === 'riding') continue;
    ctx.fillStyle = AMBIENT_JOB_COLORS[r.job] ?? TRANSIT_MAP_TRAIN_COLOR;
    ctx.fillRect(Math.round(r.x * sx - d / 2), Math.round((r.y - TILE_SIZE / 2) * sy - d / 2), d, d);
    drawnResidents++;
  }
  ctx.fillStyle = TRANSIT_MAP_TRAIN_COLOR;
  for (const tr of trains) {
    const half = (TRAIN_LENGTH_TILES * TILE_SIZE) / 2;
    const x0 = (tr.x * TILE_SIZE - half) * sx;
    const w = Math.max(3, TRAIN_LENGTH_TILES * TILE_SIZE * sx);
    ctx.fillRect(Math.round(x0), Math.round((tr.railY - TILE_SIZE / 2) * sy - WORLD_MAP_TRAIN_BAR_PX / 2), Math.round(w), WORLD_MAP_TRAIN_BAR_PX);
  }
  return { residents: drawnResidents, trains: trains.length };
}
