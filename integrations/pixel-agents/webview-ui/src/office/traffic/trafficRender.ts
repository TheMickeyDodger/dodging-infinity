/**
 * DRAWING TRAFFIC (living city 6–9): where a vehicle's art goes on the canvas, from its pose. DOM-free.
 *
 * The art is the world's own vehicle furniture, so it is placed exactly as that furniture is: a vehicle's head sits
 * on its lane tile, the bottom of the sprite is the ground row of that tile, and the front of an east- or
 * west-facing sprite is the head tile's leading edge. A sprite facing up or down is centred on its lane tile. The
 * z-order key is the bottom of the ground tile, the same as a placed piece of furniture, so vehicles sort with
 * walls, furniture and people.
 */
import { getCatalogEntry } from '../layout/furnitureCatalog.js';
import { presentationGeneration } from '../presentationGeneration.js';
import type { SpriteData } from '../types.js';
import { TILE_SIZE } from '../types.js';
import { HEADING_E, HEADING_W } from './trafficGraph.js';
import type { Vehicle } from './trafficWorld.js';
import { vehicleType } from './trafficWorld.js';

let cacheGeneration = -1;
const sprites = new Map<string, SpriteData | null>();

/** The sprite for a vehicle's kind and current heading (cached per asset generation), or null if it is not loaded. */
export function vehicleSprite(v: Pick<Vehicle, 'kind' | 'heading'>): SpriteData | null {
  const generation = presentationGeneration();
  if (generation !== cacheGeneration) {
    sprites.clear();
    cacheGeneration = generation;
  }
  const type = vehicleType(v.kind, v.heading);
  if (sprites.has(type)) return sprites.get(type) ?? null;
  const entry = getCatalogEntry(type);
  const sprite = entry && entry.sprite.length > 0 ? entry.sprite : null;
  sprites.set(type, sprite);
  return sprite;
}

export interface VehiclePlacement {
  /** Top-left in world pixels. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Depth key: the bottom edge of the ground tile. */
  zY: number;
}

export function vehiclePlacement(
  v: Pick<Vehicle, 'hx' | 'hy' | 'heading'>,
  sprite: SpriteData,
): VehiclePlacement {
  const h = sprite.length;
  const w = sprite[0]?.length ?? 0;
  const bottom = (v.hy + 1) * TILE_SIZE;
  const x =
    v.heading === HEADING_E
      ? (v.hx + 1) * TILE_SIZE - w
      : v.heading === HEADING_W
        ? v.hx * TILE_SIZE
        : v.hx * TILE_SIZE + TILE_SIZE / 2 - w / 2;
  return { x, y: bottom - h, w, h, zY: bottom };
}

/** The vehicle under a world-pixel point, or null. Callers hit-test real agents, pets and residents FIRST. */
export function vehicleAt(
  vehicles: readonly Vehicle[],
  worldX: number,
  worldY: number,
): Vehicle | null {
  let best: Vehicle | null = null;
  let bestZ = -Infinity;
  for (const v of vehicles) {
    const sprite = vehicleSprite(v);
    if (!sprite) continue;
    const p = vehiclePlacement(v, sprite);
    if (
      worldX >= p.x &&
      worldX < p.x + p.w &&
      worldY >= p.y &&
      worldY < p.y + p.h &&
      p.zY > bestZ
    ) {
      best = v;
      bestZ = p.zY;
    }
  }
  return best;
}
