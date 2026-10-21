/**
 * THE MAP'S LIVING LAYER (M3 S3, C3; traffic added in living city 6-9): ambient residents, moving trains and
 * moving traffic drawn as simple markers on the full-world Map, from the SAME live objects the canvas draws
 * (OfficeState's resident list, the transit trains and the visible vehicles) at the Map's own bounded cadence
 * (WORLD_MAP_REFRESH_MS). Markers only: residents are job-coloured dots, trains teal bars and vehicles small
 * boxes of one neutral colour (their footprint, so no vehicle reads as any kind of vehicle). Riders aboard a
 * train are part of the train's bar. Real agents and pets are drawn AFTER this layer by the Map, so they always
 * sit on top (real-agent priority). Nothing here can style a resident as an agent: no halo, no status, no name.
 *
 * WHY THE VEHICLES ARE HERE: while a vehicle drives, its parked copy is not part of the furniture the Map's
 * static layer draws (the same instances the canvas draws), so without a marker the Map would show the streets
 * emptied of vehicles. With one, the Map shows each visible vehicle once, where it is, as often as it redraws.
 */
import {
  AMBIENT_JOB_COLORS,
  TRANSIT_MAP_TRAIN_COLOR,
  WORLD_MAP_RESIDENT_DOT_PX,
  WORLD_MAP_TRAIN_BAR_PX,
  WORLD_MAP_VEHICLE_COLOR,
  WORLD_MAP_VEHICLE_MIN_PX,
} from '../constants.js';
import type { AmbientResident } from './ambient/ambientWorld.js';
import { vehiclePlacement, vehicleSprite } from './traffic/trafficRender.js';
import type { Vehicle } from './traffic/trafficWorld.js';
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
  vehicles: readonly Vehicle[] = [],
): { residents: number; trains: number; vehicles: number } {
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
  // decorative traffic: each visible vehicle's footprint (the art's own size where it is loaded, else one tile)
  ctx.fillStyle = WORLD_MAP_VEHICLE_COLOR;
  for (const v of vehicles) {
    const sprite = vehicleSprite(v);
    const p = sprite ? vehiclePlacement(v, sprite) : { x: v.hx * TILE_SIZE, y: v.hy * TILE_SIZE, w: TILE_SIZE, h: TILE_SIZE };
    ctx.fillRect(
      Math.round(p.x * sx),
      Math.round(p.y * sy),
      Math.max(WORLD_MAP_VEHICLE_MIN_PX, Math.round(p.w * sx)),
      Math.max(WORLD_MAP_VEHICLE_MIN_PX, Math.round(p.h * sy)),
    );
  }
  return { residents: drawnResidents, trains: trains.length, vehicles: vehicles.length };
}
