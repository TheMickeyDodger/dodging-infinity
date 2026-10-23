/**
 * SEASON ART (item 18, first pass): code-native recolours of the world's EXISTING reviewed art. DOM-free.
 *
 * Summer is the world exactly as it has always looked: `seasonalSprite` hands back the very same sprite, so summer costs nothing
 * and is pixel-identical. Spring, autumn and winter re-tone only two things, both outdoors:
 *   - GROUND tiles: meadow, forest floor and crops (`ground`), dirt/soil/sand (`soil`), and a light winter frost on pavement,
 *     cobbles and planks (`hard`). Roads, water, indoor floors and walls are never touched.
 *   - FOLIAGE furniture: deciduous trees, bushes, ferns, flowers, crops and reeds (`canopy`), and evergreens (`conifer`).
 * Buildings, desks, vehicles, characters, pets and residents are never recoloured.
 *
 * Recoloured sprites come from season-keyed caches that are CAPPED (CITY_SEASON_CACHE_MAX_ENTRIES, cleared whole when full) and
 * REGISTERED with the render-cache lifecycle (renderCaches.ts), so a layout reload or a restored canvas context drops them and the
 * next frame rebuilds them from the source art. A recolour is a pure function of (season, kind, source pixel), so a rebuilt
 * sprite is identical to the one it replaces.
 */
import {
  CITY_SEASON_CACHE_MAX_ENTRIES,
  CITY_SEASON_GREEN_HUE_MAX,
  CITY_SEASON_GREEN_HUE_MIN,
  CITY_SEASON_GREY_SATURATION,
  CITY_SEASON_TONES,
  CITY_SEASON_TRUNK_LIGHTNESS,
  CITY_WINTER_AREA_WASH_COLOR,
} from '../../constants.js';
import { registerRenderCache } from '../renderCaches.js';
import type { SpriteData } from '../types.js';
import { TileType } from '../types.js';
import type { Season, SeasonFrame } from './citySeason.js';

export type SeasonalKind = keyof typeof CITY_SEASON_TONES;
export type FoliageKind = 'canopy' | 'conifer' | 'crop';

const CANOPY_TYPE =
  /^(FOREST_(OAK|BIRCH|BUSH|FERN|FLOWERS)|AUTUMN_|FARM_APPLE_TREE|COAST_(REEDS|CATTAILS))/;
const CROP_TYPE = /^FARM_(WHEAT|CORN|PUMPKINS)/;
const CONIFER_TYPE = /^(FOREST_PINE|HIGHLAND_)/;
/** type -> kind, so the classification runs once per distinct type. CAPPED like every season cache (a layout controls its type
 *  strings), and dropped by the render-cache lifecycle with the rest. */
const foliageMemo = new Map<string, FoliageKind | null>();

/** Whether a furniture type is outdoor foliage the seasons recolour, and which kind (null: not foliage). */
export function foliageKindOf(type: string): FoliageKind | null {
  const known = foliageMemo.get(type);
  if (known !== undefined) return known;
  if (foliageMemo.size >= CITY_SEASON_CACHE_MAX_ENTRIES) foliageMemo.clear();
  const kind: FoliageKind | null = CANOPY_TYPE.test(type)
    ? 'canopy'
    : CONIFER_TYPE.test(type)
      ? 'conifer'
      : CROP_TYPE.test(type)
        ? 'crop'
        : null;
  foliageMemo.set(type, kind);
  return kind;
}

/** Which seasonal ground kind a tile type is, or null when the seasons leave it alone (roads, water, every indoor floor, walls). */
export function groundKindOf(tile: number): SeasonalKind | null {
  switch (tile) {
    case TileType.FLOOR_10: // meadow grass
    case TileType.FLOOR_11: // forest floor
      return 'ground';
    case TileType.FLOOR_28: // crop field rows
      return 'crop';
    case TileType.FLOOR_12: // dirt trail
    case TileType.FLOOR_21: // ploughed soil
    case TileType.FLOOR_20: // sand
      return 'soil';
    case TileType.FLOOR_13: // deck planks
    case TileType.FLOOR_18: // pavement slabs
    case TileType.FLOOR_19: // cobblestones
    case TileType.FLOOR_24: // boardwalk planks
      return 'hard';
    default:
      return null;
  }
}

// ── colour maths (self-contained: the seasons add nothing to colorize.ts) ─────

interface Hsl {
  h: number;
  s: number;
  l: number;
}

function rgbToHsl(r: number, g: number, b: number): Hsl {
  const rf = r / 255;
  const gf = g / 255;
  const bf = b / 255;
  const max = Math.max(rf, gf, bf);
  const min = Math.min(rf, gf, bf);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rf) h = (gf - bf) / d + (gf < bf ? 6 : 0);
  else if (max === gf) h = (bf - rf) / d + 2;
  else h = (rf - gf) / d + 4;
  return { h: h * 60, s, l };
}

function hue2rgb(p: number, q: number, t: number): number {
  let u = t;
  if (u < 0) u += 1;
  if (u > 1) u -= 1;
  if (u < 1 / 6) return p + (q - p) * 6 * u;
  if (u < 1 / 2) return q;
  if (u < 2 / 3) return p + (q - p) * (2 / 3 - u) * 6;
  return p;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hf = (((h % 360) + 360) % 360) / 360;
  return [
    Math.round(hue2rgb(p, q, hf + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, hf) * 255),
    Math.round(hue2rgb(p, q, hf - 1 / 3) * 255),
  ];
}

const clamp01 = (v: number): number => Math.max(0, Math.min(1, v));
const hex2 = (v: number): string => v.toString(16).padStart(2, '0').toUpperCase();

function lerpHue(from: number, to: number, t: number): number {
  const delta = ((to - from + 540) % 360) - 180;
  return from + delta * t;
}

/** Apply a tone to one HSL colour. */
function applyTone(
  hsl: Hsl,
  tone: {
    hueTo: number;
    hueBlend: number;
    satMul: number;
    satMax: number;
    lightMul: number;
    lightAdd: number;
  },
): Hsl {
  return {
    h: tone.hueBlend > 0 ? lerpHue(hsl.h, tone.hueTo, tone.hueBlend) : hsl.h,
    s: Math.min(tone.satMax, clamp01(hsl.s * tone.satMul)),
    l: clamp01(hsl.l * tone.lightMul + tone.lightAdd),
  };
}

/** The recoloured pixel for one source pixel ('' stays transparent; an alpha byte is kept). */
function recolorPixel(
  pixel: string,
  season: Exclude<Season, 'summer'>,
  kind: SeasonalKind,
): string {
  if (pixel === '' || pixel.length < 7) return pixel;
  const r = parseInt(pixel.slice(1, 3), 16);
  const g = parseInt(pixel.slice(3, 5), 16);
  const b = parseInt(pixel.slice(5, 7), 16);
  const hsl = rgbToHsl(r, g, b);
  const foliage = kind === 'canopy' || kind === 'conifer' || kind === 'crop';
  if (foliage) {
    // greys (rock, shadow) and dark brown (trunk, soil) are not foliage: leave them as they are
    if (hsl.s <= CITY_SEASON_GREY_SATURATION) return pixel;
    if (hsl.l < CITY_SEASON_TRUNK_LIGHTNESS && hsl.h >= 10 && hsl.h <= 50) return pixel;
  }
  const tones = CITY_SEASON_TONES[kind][season];
  const green = hsl.h >= CITY_SEASON_GREEN_HUE_MIN && hsl.h <= CITY_SEASON_GREEN_HUE_MAX;
  const tone = green ? tones.green : tones.warm;
  // an identity tone leaves the pixel exactly as it was (no rounding drift through HSL and back)
  if (
    tone.hueBlend === 0 &&
    tone.satMul === 1 &&
    tone.satMax === 1 &&
    tone.lightMul === 1 &&
    tone.lightAdd === 0
  )
    return pixel;
  const out = applyTone(hsl, tone);
  const [nr, ng, nb] = hslToRgb(out.h, out.s, out.l);
  // recoloured pixels follow the repo's uppercase hex convention (colorize.ts)
  return `#${hex2(nr)}${hex2(ng)}${hex2(nb)}${pixel.length > 7 ? pixel.slice(7, 9).toUpperCase() : ''}`;
}

// ── the district wash over winter ground ──────────────────────────────────────

/** How much of winter is showing in a season frame, 0..1: 1 in a settled winter, ramping with the cross-fade into and out of it, 0 otherwise (and with no season layer). */
export function winterWeight(frame: SeasonFrame | null): number {
  if (!frame) return 0;
  if (frame.season === 'winter') return frame.from === null ? 1 : frame.t;
  return frame.from === 'winter' ? 1 - frame.t : 0;
}

/**
 * The colour a district's wash is drawn in over a GROUND tile (meadow, forest floor) while winter shows, at `weight` (0..1). A saturated WARM wash
 * (within 60 degrees of red, either side: orange, tan, rose) is mixed toward the cool neutral CITY_WINTER_AREA_WASH_COLOR, so snow reads as snow
 * in every district and no warm/cool seam forms at a district border. Anything else (a cool, green or grey wash, a colour that is not #RRGGBB) and
 * a weight of 0 return `color` itself, the very same string, so every other season and every cool district is exactly what it was.
 */
export function winterAreaWash(color: string, weight: number): string {
  if (weight <= 0 || !/^#[0-9a-fA-F]{6}$/.test(color)) return color;
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  const hsl = rgbToHsl(r, g, b);
  if (
    hsl.s <= CITY_SEASON_GREY_SATURATION ||
    Math.min(hsl.h, 360 - hsl.h) >= CITY_SEASON_GREEN_HUE_MIN
  )
    return color;
  const to = CITY_WINTER_AREA_WASH_COLOR;
  const mix = (from: number, i: number): string =>
    hex2(Math.round(from + (parseInt(to.slice(i, i + 2), 16) - from) * Math.min(1, weight)));
  return `#${mix(r, 1)}${mix(g, 3)}${mix(b, 5)}`;
}

// ── caches (capped; registered with the render-cache lifecycle) ───────────────

const spriteCaches = new Map<string, Map<SpriteData, SpriteData>>();
const pixelMemos = new Map<string, Map<string, string>>();

/** Drop every recoloured sprite and every per-colour memo: the next frame rebuilds them from the source art. */
export function resetSeasonCaches(): void {
  spriteCaches.clear();
  pixelMemos.clear();
  foliageMemo.clear();
}

// joins the lifecycle (renderCaches.ts does not import the city modules); it holds no carpet-layer pixels, so it reports none
registerRenderCache({ reset: resetSeasonCaches, pixels: () => 0 });

/** Entries held across every season cache (render-cache accounting; tests). */
export function seasonCacheEntries(): { sprites: number; pixels: number; types: number } {
  let sprites = 0;
  for (const c of spriteCaches.values()) sprites += c.size;
  let pixels = 0;
  for (const m of pixelMemos.values()) pixels += m.size;
  return { sprites, pixels, types: foliageMemo.size };
}

function recolorSprite(
  sprite: SpriteData,
  season: Exclude<Season, 'summer'>,
  kind: SeasonalKind,
): SpriteData {
  const key = `${season}|${kind}`;
  let memo = pixelMemos.get(key);
  if (!memo) {
    memo = new Map();
    pixelMemos.set(key, memo);
  }
  const out: SpriteData = [];
  for (const row of sprite) {
    const next: string[] = [];
    for (const pixel of row) {
      if (pixel === '') {
        next.push('');
        continue;
      }
      let mapped = memo.get(pixel);
      if (mapped === undefined) {
        mapped = recolorPixel(pixel, season, kind);
        // EVERY insertion is bounded, not just the start of a sprite: one sprite can bring many new colours, and the memo must never
        // stand above its cap (it is cleared whole when full; a memo is only a cache of a pure function, so the output is the same)
        if (memo.size >= CITY_SEASON_CACHE_MAX_ENTRIES) memo.clear();
        memo.set(pixel, mapped);
      }
      next.push(mapped);
    }
    out.push(next);
  }
  return out;
}

/**
 * The sprite to draw for `sprite` in `season`. Summer returns `sprite` itself (the world as it has always looked); another season
 * returns a recoloured copy, cached by the source sprite's identity under the cap.
 */
export function seasonalSprite(season: Season, kind: SeasonalKind, sprite: SpriteData): SpriteData {
  if (season === 'summer') return sprite;
  const key = `${season}|${kind}`;
  let cache = spriteCaches.get(key);
  if (!cache) {
    cache = new Map();
    spriteCaches.set(key, cache);
  }
  const hit = cache.get(sprite);
  if (hit) return hit;
  if (cache.size >= CITY_SEASON_CACHE_MAX_ENTRIES) cache.clear();
  const built = recolorSprite(sprite, season, kind);
  cache.set(sprite, built);
  return built;
}
