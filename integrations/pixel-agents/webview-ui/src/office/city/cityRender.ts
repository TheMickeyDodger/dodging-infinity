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
  CITY_HEAVY_SNOW_NEAR_DEPTH,
  CITY_HEAVY_SNOW_SIZE_PX,
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
  CITY_SNOW_FLAKE_SCALE,
  CITY_SNOW_HALO_COLOR,
  CITY_SNOW_HALO_PX,
  CITY_SNOW_SIZE_PX,
  CITY_WEATHER_PARTICLE_TOTAL,
  cityRgba,
} from '../../constants.js';
import type { VisibleTiles } from '../projection.js';
import { registerRenderCache } from '../renderCaches.js';
import { TILE_SIZE } from '../types.js';
import type { DarknessColor } from './cityLight.js';
import { levelColor } from './cityLight.js';
import type { CityMask, LightPoint } from './cityMask.js';
import { indoorRuns, MASK_NONE, MASK_OUTDOOR, maskAt } from './cityMask.js';
import type { RegionMap } from './cityRegions.js';
import { blendStrength, edgeWeight, REGION_NONE, regionAt, regionEdges } from './cityRegions.js';
import type { RegionalWeather, WeatherKind } from './cityRegionWeather.js';
import { WEATHER_KINDS } from './cityRegionWeather.js';
import type { SeasonFrame } from './citySeason.js';
import type { WeatherField } from './cityWeather.js';
import { weatherVeil } from './cityWeather.js';

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
  resetCityLayers();
}

/** The render-cache lifecycle (renderCaches.ts): drop the cached surfaces. The two light sprites are drawn ONCE and kept, so they
 *  must be rebuilt after a context restore; the darkness layer is cleared and redrawn every frame. */
export function resetCityLayers(): void {
  darkness = null;
  sprites = null;
}

// joins the lifecycle (renderCaches.ts does not import this DOM module); it holds no carpet-layer pixels, so it reports none
registerRenderCache({ reset: resetCityLayers, pixels: () => 0 });

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

/** The regional weather to draw: what each region shows (fields, fades) and the world-coordinate map saying which region a tile is in. */
export interface RegionalRenderInput {
  weather: RegionalWeather;
  regions: RegionMap;
}

/** What the renderer needs to draw the city's season, light and weather this frame (built by OfficeState). */
export interface CityRenderInput {
  /** Darkness level shown now, 0 (day) .. 1 (night). 0 draws no lighting. */
  level: number;
  mask: CityMask;
  lamps: readonly LightPoint[];
  agents: readonly LightPoint[];
  /** The regional weather to draw (its veil per region, its particles over outdoor tiles of the region showing them), or null for none. */
  weather: RegionalRenderInput | null;
  /** The season to draw (null: plain summer, the world as it has always looked). */
  season: SeasonFrame | null;
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
  /** One uniform veil over the whole canvas, laid under the same indoor and light-pool holes; null for none. */
  veil?: DarknessColor | null;
  /** The regional weather's veil, laid per region over that region's tiles only, under the same holes; null for none. */
  regional?: RegionalRenderInput | null;
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

/** The veil colour of each kind, as a function of the kind (rain, light snow, heavy snow). Clear has none. */
function kindVeil(kind: WeatherKind): DarknessColor | null {
  return weatherVeil(
    kind === 'rain'
      ? 'rain'
      : kind === 'light-snow'
        ? 'snow'
        : kind === 'heavy-snow'
          ? 'heavy-snow'
          : 'off',
  );
}

/**
 * Lay the regional weather's veil over the VISIBLE tiles, run by run: each maximal run of one region in a row gets one fill per kind
 * that region is showing (a fade shows two), at that kind's veil colour scaled by its fade. The fills are tile-aligned rectangles
 * in the darkness layer, so the indoor holes and agent light pools punched afterwards cut through them exactly as through night.
 */
function fillRegionVeils(
  d: CityCtx,
  input: RegionalRenderInput,
  frame: CityFrame,
  mask: CityMask,
): void {
  const { weather, regions } = input;
  const { offsetX, offsetY, zoom, view } = frame;
  const tile = TILE_SIZE * zoom;
  const edges = regionEdges(regions);
  /** The fill styles of a region, or of an EDGE tile of it leaning `w` toward a foreign region: each kind at its blended strength. */
  const stylesFor = (slot: number, foreign: number, w: number): string[] => {
    const list: string[] = [];
    for (const kind of WEATHER_KINDS) {
      const veil = kindVeil(kind);
      if (!veil) continue;
      const a = blendStrength(
        weather.alpha(kind, slot),
        w > 0 ? weather.alpha(kind, foreign) : 0,
        w,
      );
      if (a > 0) list.push(cityRgba(veil.r, veil.g, veil.b, veil.a * a));
    }
    return list;
  };
  // one style list per (region, foreign region, edge distance) the frame uses, built on first use (a handful at most), and a small integer id per
  // DISTINCT list of styles: tiles with the same styles share an id, so a run spans them whatever their region or edge distance (equal weather on
  // both sides of a border lays one fill across it: no seam)
  const memo = new Map<number, number>();
  const lists: string[][] = [];
  const idOfList = new Map<string, number>();
  const idOf = (slot: number, dist: number, foreign: number): number => {
    const key = (slot << 16) | (dist << 8) | foreign;
    let id = memo.get(key);
    if (id === undefined) {
      const list = stylesFor(slot, foreign, edgeWeight(dist));
      const joined = list.join('|');
      id = idOfList.get(joined);
      if (id === undefined) {
        id = lists.length;
        lists.push(list);
        idOfList.set(joined, id);
      }
      memo.set(key, id);
    }
    return id;
  };
  const lo = Math.max(0, view.c0);
  const hi = Math.min(regions.cols, view.c1);
  for (let r = Math.max(0, view.r0); r < Math.min(regions.rows, view.r1); r++) {
    const base = r * regions.cols;
    const idAt = (c: number): number => {
      if (c >= hi) return -1;
      const i = base + c;
      const slot = regions.cells[i];
      if (slot === REGION_NONE || slot >= regions.slots) return -1;
      // no weather veil over empty ground (nothing is there to be under a sky); the indoors is punched out afterwards
      if (maskAt(mask, c, r) === MASK_NONE) return -1;
      const dist = edges.dist[i];
      return idOf(slot, dist, dist > 0 ? edges.foreign[i] : 0);
    };
    let start = lo;
    let id = lo < hi ? idAt(lo) : -1;
    for (let c = lo + 1; c <= hi; c++) {
      const here = idAt(c);
      if (c < hi && here === id) continue;
      if (id >= 0) {
        for (const style of lists[id]) {
          d.fillStyle = style;
          d.fillRect(offsetX + start * tile, offsetY + r * tile, (c - start) * tile, tile);
        }
      }
      start = c;
      id = here;
    }
  }
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
  const regional = input.regional && input.regional.weather.anyWeather() ? input.regional : null;
  if (!lit && !veil && !regional) return null;
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
  if (regional) fillRegionVeils(d, regional, frame, input.mask);

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

/** Which tiles a field is drawn over (item 18): the region map, and how strongly the field's kind shows in each region slot. */
export interface WeatherRegionFilter {
  regions: RegionMap;
  alpha(slot: number): number;
}

/** Whether the rectangle (x, y, w, h) on the canvas lies wholly over OUTDOOR tiles: all four corners are (a rectangle smaller than a tile spans at most 2 x 2). */
function overOpenGround(
  mask: CityMask,
  x: number,
  y: number,
  w: number,
  h: number,
  offsetX: number,
  offsetY: number,
  tile: number,
): boolean {
  const c0 = Math.floor((x - offsetX) / tile);
  const r0 = Math.floor((y - offsetY) / tile);
  const c1 = Math.floor((x + w - 1 - offsetX) / tile);
  const r1 = Math.floor((y + h - 1 - offsetY) / tile);
  return (
    maskAt(mask, c0, r0) === MASK_OUTDOOR &&
    maskAt(mask, c1, r0) === MASK_OUTDOOR &&
    maskAt(mask, c0, r1) === MASK_OUTDOOR &&
    maskAt(mask, c1, r1) === MASK_OUTDOOR
  );
}

const wrapInto = (v: number, n: number): number => {
  const m = v % n;
  return m < 0 ? m + n : m;
};

/**
 * WHERE A PARTICLE IS DRAWN (D5): its base position on the canvas shifted by the camera's offset and wrapped over the canvas, so a PAN
 * carries every particle with the world: it stays over the same world point (and so the same tile, indoor or outdoor, and the same region)
 * until it leaves the canvas and re-enters on the other side. The world point under a drawn particle, in canvas pixels, is its position
 * minus the offset. (A zoom change rescales the offsets, so the particles re-seat on a zoom: that is not a pan.)
 */
export function particleScreenX(
  xNorm: number,
  frame: Pick<CityFrame, 'canvasWidth' | 'offsetX'>,
): number {
  return Math.floor(wrapInto(xNorm * frame.canvasWidth + frame.offsetX, frame.canvasWidth));
}
export function particleScreenY(
  yNorm: number,
  frame: Pick<CityFrame, 'canvasHeight' | 'offsetY'>,
): number {
  return Math.floor(wrapInto(yNorm * frame.canvasHeight + frame.offsetY, frame.canvasHeight));
}

/** The flakes of a light-snow pass, collected so the cool shadows are laid in one pass and the white flakes in the next (bounded by the pool). */
const flakeX = new Int32Array(CITY_WEATHER_PARTICLE_TOTAL);
const flakeY = new Int32Array(CITY_WEATHER_PARTICLE_TOTAL);
const flakeSize = new Int32Array(CITY_WEATHER_PARTICLE_TOTAL);
const flakeAlpha = new Float32Array(CITY_WEATHER_PARTICLE_TOTAL);
/** 1 when the flake's whole shadow lies over outdoor tiles (a shadow never reaches an indoor tile or empty ground). */
const flakeShadow = new Uint8Array(CITY_WEATHER_PARTICLE_TOTAL);

/**
 * Draw the weather over the outdoors. Returns the number of particles actually drawn. With a `region` filter a particle is also
 * drawn only where the tile under it lies in a region showing this field's kind (at that region's fade strength, leaning toward the
 * neighbouring region across the soft edge); the region comes from the WORLD tile under the particle, so it never depends on the camera.
 */
export function renderCityWeather(
  ctx: CityCtx,
  weather: WeatherField,
  mask: CityMask,
  frame: CityFrame,
  region?: WeatherRegionFilter,
): number {
  const n = Math.min(weather.count, CITY_WEATHER_PARTICLE_TOTAL);
  if (n === 0) return 0;
  const { zoom, offsetX, offsetY } = frame;
  const tile = TILE_SIZE * zoom;
  const rain = weather.kind === 'rain';
  const heavy = weather.kind === 'heavy-snow';
  const edges = region ? regionEdges(region.regions) : null;
  ctx.save();
  ctx.fillStyle = rain ? CITY_RAIN_COLOR : CITY_SNOW_COLOR;
  const rainWidth = Math.max(1, Math.round(zoom)); // one sprite pixel wide
  let drawn = 0;
  let shownAlpha = 1;
  let flakes = 0;
  for (let i = 0; i < n; i++) {
    const px = particleScreenX(weather.xs[i], frame);
    const py = particleScreenY(weather.ys[i], frame);
    const col = Math.floor((px - offsetX) / tile);
    const row = Math.floor((py - offsetY) / tile);
    // indoor protection: only a particle over an OUTDOOR tile is drawn
    if (maskAt(mask, col, row) !== MASK_OUTDOOR) continue;
    let a = 1;
    if (region) {
      const slot = regionAt(region.regions, col, row);
      a = slot === REGION_NONE ? 0 : region.alpha(slot);
      const at = row * region.regions.cols + col;
      if (edges && slot !== REGION_NONE && edges.dist[at] > 0) {
        a = blendStrength(a, region.alpha(edges.foreign[at]), edgeWeight(edges.dist[at]));
      }
      if (a <= 0) continue;
    }
    const depth = weather.depth[i];
    if (rain || heavy) {
      if (a !== shownAlpha) {
        ctx.globalAlpha = a;
        shownAlpha = a;
      }
      if (rain) {
        let length = Math.max(2, Math.round(CITY_RAIN_LENGTH_PX * zoom * depth));
        // a streak is drawn only over open ground, like a flake: one that would run into the tile below (an indoor tile, such as a doorway
        // floor, or empty ground) is cut at the edge of the tile it starts in, and one that is still not wholly over open ground (it
        // straddles a column) is not drawn
        if (!overOpenGround(mask, px, py, rainWidth, length, offsetX, offsetY, tile)) {
          length = Math.min(length, Math.floor((row + 1) * tile + offsetY - py));
          if (
            length < 1 ||
            !overOpenGround(mask, px, py, rainWidth, length, offsetX, offsetY, tile)
          )
            continue;
        }
        ctx.fillRect(px, py, rainWidth, length);
      } else {
        // two layers: the near one is drawn half as large again
        const near = depth >= CITY_HEAVY_SNOW_NEAR_DEPTH ? 1.5 : 1;
        const size = Math.max(1, Math.round(CITY_HEAVY_SNOW_SIZE_PX * zoom * depth * 1.4 * near));
        // a flake is drawn only if all of it is over open ground (it never spills into an indoor tile or onto empty ground)
        if (!overOpenGround(mask, px, py, size, size, offsetX, offsetY, tile)) continue;
        ctx.fillRect(px, py, size, size);
      }
    } else {
      const size = Math.max(
        1,
        Math.round(CITY_SNOW_SIZE_PX * zoom * depth * CITY_SNOW_FLAKE_SCALE),
      );
      // the flake is drawn only if all of it is over open ground; its shadow (one screen pixel wider on every side) only if all of THAT is
      if (!overOpenGround(mask, px, py, size, size, offsetX, offsetY, tile)) continue;
      flakeX[flakes] = px;
      flakeY[flakes] = py;
      flakeSize[flakes] = size;
      flakeAlpha[flakes] = a;
      flakeShadow[flakes] = overOpenGround(
        mask,
        px - CITY_SNOW_HALO_PX,
        py - CITY_SNOW_HALO_PX,
        size + 2 * CITY_SNOW_HALO_PX,
        size + 2 * CITY_SNOW_HALO_PX,
        offsetX,
        offsetY,
        tile,
      )
        ? 1
        : 0;
      flakes++;
    }
    drawn++;
  }
  if (flakes > 0) {
    // light snow: a small cool shadow under each flake (laid first), then the white flakes, so a flake reads on white ground and on forest
    const halo = CITY_SNOW_HALO_PX;
    ctx.fillStyle = CITY_SNOW_HALO_COLOR;
    for (let i = 0; i < flakes; i++) {
      if (flakeShadow[i] === 0) continue;
      if (flakeAlpha[i] !== shownAlpha) {
        ctx.globalAlpha = flakeAlpha[i];
        shownAlpha = flakeAlpha[i];
      }
      ctx.fillRect(
        flakeX[i] - halo,
        flakeY[i] - halo,
        flakeSize[i] + 2 * halo,
        flakeSize[i] + 2 * halo,
      );
    }
    ctx.fillStyle = CITY_SNOW_COLOR;
    for (let i = 0; i < flakes; i++) {
      if (flakeAlpha[i] !== shownAlpha) {
        ctx.globalAlpha = flakeAlpha[i];
        shownAlpha = flakeAlpha[i];
      }
      ctx.fillRect(flakeX[i], flakeY[i], flakeSize[i], flakeSize[i]);
    }
  }
  ctx.restore();
  return drawn;
}

/** Draw every region's weather: each kind's field over the outdoor tiles of the regions showing that kind. Returns the particles drawn. */
export function renderRegionalWeather(
  ctx: CityCtx,
  input: RegionalRenderInput,
  mask: CityMask,
  frame: CityFrame,
): number {
  let drawn = 0;
  for (const kind of WEATHER_KINDS) {
    const field = input.weather.fieldFor(kind);
    if (!field || field.count === 0) continue;
    drawn += renderCityWeather(ctx, field, mask, frame, {
      regions: input.regions,
      alpha: (slot) => input.weather.alpha(kind, slot),
    });
  }
  return drawn;
}
