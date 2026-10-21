/**
 * DRAWING THE CITY'S LIGHT AND WEATHER (living city 6–9). Two passes the renderer calls between the
 * z-sorted scene and the speech bubbles/labels, so bubbles, labels, selection chrome and the DOM
 * inspectors are never darkened:
 *
 * - LIGHTING: an offscreen DARKNESS layer the size of the canvas. It is filled with the stage colour,
 *   then holes are punched out of it: the INDOOR tiles (offices keep their lights on) and soft LIGHT POOLS
 *   around every real agent and every street lamp in view. The layer is then laid over the scene, and
 *   lamps get a faint warm glow. Day draws nothing and costs nothing.
 * - WEATHER: seeded particles drawn only where the tile under them is OUTDOORS.
 *
 * Both are bounded per frame: at most CITY_LIGHT_MAX_LIGHTS lights (agents first), the indoor rectangles
 * of the visible tiles only, and a fixed particle count. A canvas that cannot be created (no document)
 * means the pass is skipped, never an error. DOM-free apart from the default layer factory, which a test
 * replaces.
 */
import {
  CITY_LIGHT_AGENT_RADIUS_TILES,
  CITY_LIGHT_AGENT_STRENGTH,
  CITY_LIGHT_INDOOR_LIT,
  CITY_LIGHT_LAMP_GLOW,
  CITY_LIGHT_LAMP_GLOW_ALPHA,
  CITY_LIGHT_LAMP_RADIUS_TILES,
  CITY_LIGHT_LAMP_STRENGTH,
  CITY_LIGHT_MAX_LIGHTS,
  CITY_LIGHT_SPRITE_PX,
  CITY_LIGHT_VIEW_MARGIN_TILES,
  CITY_LIGHT_VISIBLE_EPSILON,
  CITY_RAIN_COLOR,
  CITY_RAIN_LENGTH_PX,
  CITY_SNOW_COLOR,
  CITY_SNOW_SIZE_PX,
  cityRgba,
} from '../../constants.js';
import type { VisibleTiles } from '../projection.js';
import { TILE_SIZE } from '../types.js';
import type { DarknessColor } from './cityLight.js';
import { levelColor } from './cityLight.js';
import type { CityMask, LightPoint } from './cityMask.js';
import { indoorRuns, MASK_OUTDOOR, maskAt } from './cityMask.js';
import type { WeatherField } from './cityWeather.js';

/** The slice of a 2D context the city layers use (a real context satisfies it; a test can record calls). */
export interface CityCtx {
  globalCompositeOperation: string;
  globalAlpha: number;
  fillStyle: unknown;
  imageSmoothingEnabled: boolean;
  save(): void;
  restore(): void;
  clearRect(x: number, y: number, w: number, h: number): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  drawImage(image: unknown, dx: number, dy: number, dw: number, dh: number): void;
  createRadialGradient(
    x0: number,
    y0: number,
    r0: number,
    x1: number,
    y1: number,
    r1: number,
  ): { addColorStop(offset: number, color: string): void };
}

/** An offscreen surface: a canvas element satisfies it. */
export interface CityLayer {
  width: number;
  height: number;
  getContext(kind: '2d'): CityCtx | null;
}
export type CityLayerFactory = (width: number, height: number) => CityLayer | null;

function defaultLayerFactory(width: number, height: number): CityLayer | null {
  const doc = (globalThis as { document?: { createElement(tag: string): unknown } }).document;
  if (!doc) return null;
  const canvas = doc.createElement('canvas') as CityLayer;
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

let layerFactory: CityLayerFactory = defaultLayerFactory;
let darkness: { layer: CityLayer; ctx: CityCtx } | null = null;
let sprites: { white: CityLayer; warm: CityLayer } | null = null;

/** Replace the surface factory (tests); null restores the default. Drops every cached surface. */
export function setCityLayerFactory(factory: CityLayerFactory | null): void {
  layerFactory = factory ?? defaultLayerFactory;
  darkness = null;
  sprites = null;
}

function ensureDarkness(width: number, height: number): { layer: CityLayer; ctx: CityCtx } | null {
  if (darkness && darkness.layer.width === width && darkness.layer.height === height)
    return darkness;
  const layer = layerFactory(width, height);
  const ctx = layer?.getContext('2d') ?? null;
  darkness = layer && ctx ? { layer, ctx } : null;
  return darkness;
}

function buildSprite(rgb: { r: number; g: number; b: number }): CityLayer | null {
  const layer = layerFactory(CITY_LIGHT_SPRITE_PX, CITY_LIGHT_SPRITE_PX);
  const ctx = layer?.getContext('2d') ?? null;
  if (!layer || !ctx) return null;
  const half = CITY_LIGHT_SPRITE_PX / 2;
  const gradient = ctx.createRadialGradient(half, half, 0, half, half, half);
  gradient.addColorStop(0, cityRgba(rgb.r, rgb.g, rgb.b, 1));
  gradient.addColorStop(0.45, cityRgba(rgb.r, rgb.g, rgb.b, 0.55));
  gradient.addColorStop(1, cityRgba(rgb.r, rgb.g, rgb.b, 0));
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, CITY_LIGHT_SPRITE_PX, CITY_LIGHT_SPRITE_PX);
  return layer;
}

function ensureSprites(): { white: CityLayer; warm: CityLayer } | null {
  if (sprites) return sprites;
  const white = buildSprite({ r: 255, g: 255, b: 255 });
  const warm = buildSprite(CITY_LIGHT_LAMP_GLOW);
  sprites = white && warm ? { white, warm } : null;
  return sprites;
}

/** What the renderer needs to draw the city's light and weather this frame (built by OfficeState). */
export interface CityRenderInput {
  /** Darkness level shown now, 0 (day) .. 1 (night). 0 draws no lighting. */
  level: number;
  mask: CityMask;
  lamps: readonly LightPoint[];
  agents: readonly LightPoint[];
  /** The weather to draw, or null for none. */
  weather: WeatherField | null;
  /** The weather's faint veil over the outdoors, or null for clear skies. */
  veil: DarknessColor | null;
}

/** Where and how the frame is drawn (the renderer's own numbers). */
export interface CityFrame {
  canvasWidth: number;
  canvasHeight: number;
  offsetX: number;
  offsetY: number;
  zoom: number;
  view: VisibleTiles;
}

export interface CityLightingInput {
  /** The darkness level shown now, 0 (day) .. 1 (night). */
  level: number;
  /** The weather's veil over the outdoors, laid under the same indoor and light-pool holes; null for none. */
  veil?: DarknessColor | null;
  mask: CityMask;
  lamps: readonly LightPoint[];
  /** Real agents and sub-agents: each gets a light pool so it stays legible. */
  agents: readonly LightPoint[];
}

export interface CityLightingStats {
  /** Light pools punched (agents then lamps), at most CITY_LIGHT_MAX_LIGHTS. */
  lights: number;
  /** Warm lamp glows drawn. */
  glows: number;
  /** Indoor rectangles punched. */
  indoorRects: number;
}

/** Lay the lighting over the scene. Returns what was drawn, or null when there was nothing to draw. */
export function renderCityLighting(
  ctx: CityCtx,
  input: CityLightingInput,
  frame: CityFrame,
): CityLightingStats | null {
  const color = levelColor(input.level);
  const lit = input.level >= CITY_LIGHT_VISIBLE_EPSILON && color.a > 0;
  const veil = input.veil && input.veil.a > 0 ? input.veil : null;
  if (!lit && !veil) return null;
  const { canvasWidth: w, canvasHeight: h, offsetX, offsetY, zoom, view } = frame;
  const dark = ensureDarkness(w, h);
  const pool = ensureSprites();
  if (!dark || !pool) return null;

  const d = dark.ctx;
  const tile = TILE_SIZE * zoom;
  d.save();
  d.globalCompositeOperation = 'source-over';
  d.globalAlpha = 1;
  d.clearRect(0, 0, w, h);
  if (lit) {
    d.fillStyle = cityRgba(color.r, color.g, color.b, color.a);
    d.fillRect(0, 0, w, h);
  }
  if (veil) {
    d.fillStyle = cityRgba(veil.r, veil.g, veil.b, veil.a);
    d.fillRect(0, 0, w, h);
  }

  // Holes: the indoors (offices keep their lights on), then the light pools.
  d.globalCompositeOperation = 'destination-out';
  d.fillStyle = cityRgba(0, 0, 0, CITY_LIGHT_INDOOR_LIT);
  let indoorRects = 0;
  indoorRuns(input.mask, view.c0, view.c1, view.r0, view.r1, (row, from, to) => {
    d.fillRect(offsetX + from * tile, offsetY + row * tile, (to - from) * tile, tile);
    indoorRects++;
  });

  const margin = CITY_LIGHT_VIEW_MARGIN_TILES * TILE_SIZE;
  const left = -offsetX / zoom - margin;
  const right = (w - offsetX) / zoom + margin;
  const top = -offsetY / zoom - margin;
  const bottom = (h - offsetY) / zoom + margin;
  const inView = (p: LightPoint): boolean =>
    p.x >= left && p.x <= right && p.y >= top && p.y <= bottom;

  let lights = 0;
  const lampsDrawn: LightPoint[] = [];
  const punch = (p: LightPoint, radiusTiles: number, strength: number): void => {
    const r = radiusTiles * tile;
    d.globalAlpha = strength;
    d.drawImage(pool.white, offsetX + p.x * zoom - r, offsetY + p.y * zoom - r, r * 2, r * 2);
    lights++;
  };
  d.imageSmoothingEnabled = true;
  for (const p of input.agents) {
    if (lights >= CITY_LIGHT_MAX_LIGHTS) break;
    if (inView(p)) punch(p, CITY_LIGHT_AGENT_RADIUS_TILES, CITY_LIGHT_AGENT_STRENGTH);
  }
  // lamps give light at night only: never in a daytime shower
  for (const p of lit ? input.lamps : []) {
    if (lights >= CITY_LIGHT_MAX_LIGHTS) break;
    if (inView(p)) {
      punch(p, CITY_LIGHT_LAMP_RADIUS_TILES, CITY_LIGHT_LAMP_STRENGTH);
      lampsDrawn.push(p);
    }
  }
  d.restore();

  // Lay the darkness over the scene, then add the warm glow of the lamps that were lit.
  ctx.save();
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;
  ctx.drawImage(dark.layer, 0, 0, w, h);
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = CITY_LIGHT_LAMP_GLOW_ALPHA * (lit ? input.level : 0);
  ctx.imageSmoothingEnabled = true;
  const glowRadius = CITY_LIGHT_LAMP_RADIUS_TILES * tile;
  for (const p of lampsDrawn) {
    ctx.drawImage(
      pool.warm,
      offsetX + p.x * zoom - glowRadius,
      offsetY + p.y * zoom - glowRadius,
      glowRadius * 2,
      glowRadius * 2,
    );
  }
  ctx.restore();
  return { lights, glows: lampsDrawn.length, indoorRects };
}

/** Draw the weather over the outdoors. Returns the number of particles actually drawn. */
export function renderCityWeather(
  ctx: CityCtx,
  weather: WeatherField,
  mask: CityMask,
  frame: CityFrame,
): number {
  const n = weather.count;
  if (n === 0) return 0;
  const { canvasWidth: w, canvasHeight: h, offsetX, offsetY, zoom } = frame;
  const tile = TILE_SIZE * zoom;
  const rain = weather.kind === 'rain';
  ctx.save();
  ctx.fillStyle = rain ? CITY_RAIN_COLOR : CITY_SNOW_COLOR;
  const rainWidth = Math.max(1, Math.round(zoom)); // one sprite pixel wide
  let drawn = 0;
  for (let i = 0; i < n; i++) {
    const px = Math.floor(weather.xs[i] * w);
    const py = Math.floor(weather.ys[i] * h);
    // indoor protection: only a particle over an OUTDOOR tile is drawn
    if (
      maskAt(mask, Math.floor((px - offsetX) / tile), Math.floor((py - offsetY) / tile)) !==
      MASK_OUTDOOR
    )
      continue;
    const depth = weather.depth[i];
    if (rain) {
      ctx.fillRect(px, py, rainWidth, Math.max(2, Math.round(CITY_RAIN_LENGTH_PX * zoom * depth)));
    } else {
      const size = Math.max(1, Math.round(CITY_SNOW_SIZE_PX * zoom * depth * 1.4));
      ctx.fillRect(px, py, size, size);
    }
    drawn++;
  }
  ctx.restore();
  return drawn;
}
