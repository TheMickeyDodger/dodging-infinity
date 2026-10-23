/**
 * DECORATIVE WEATHER (living city 6–9): a fixed number of seeded particles in SCREEN space, normalised
 * to [0, 1) so the field fits any canvas. It is not a forecast and has no location: choosing rain or snow
 * only chooses which decoration is drawn over the outdoors (the renderer skips every particle that is not
 * over an outdoor tile, which is the indoor protection).
 *
 * BOUNDED: the field is allocated once at its largest kind (CITY_RAIN_PARTICLES / CITY_SNOW_PARTICLES);
 * a kind only chooses how many of those are live. Nothing grows with the world, the zoom or the time open.
 * DETERMINISTIC: every particle comes from a fixed seed, so the same steps produce the same field.
 * REDUCED MOTION: `update` is simply not called, so the field is one still frame.
 */
import type { CityWeather } from '../../../../core/src/cityPresentation.js';
import {
  CITY_HEAVY_SNOW_PARTICLES,
  CITY_HEAVY_SNOW_SPEED,
  CITY_HEAVY_SNOW_SWAY,
  CITY_OVERCAST_HEAVY_SNOW,
  CITY_OVERCAST_RAIN,
  CITY_OVERCAST_SNOW,
  CITY_RAIN_DRIFT,
  CITY_RAIN_PARTICLES,
  CITY_RAIN_SPEED,
  CITY_SNOW_PARTICLES,
  CITY_SNOW_SPEED,
  CITY_SNOW_SWAY,
  CITY_WEATHER_SEED,
} from '../../constants.js';
import { SeededRng } from '../ambient/ambientRng.js';

/** What a field can show: the saved contract's kinds plus heavy snow (its own, denser field; item 18). */
export type FieldKind = CityWeather | 'heavy-snow';

/** The default capacity: the largest of the original kinds. A heavy-snow field is built with its own, larger capacity. */
const CAPACITY = Math.max(CITY_RAIN_PARTICLES, CITY_SNOW_PARTICLES);
/** Sway phase speed of a snowflake, in radians per second at full depth. */
export const SNOW_PHASE_RATE = 1.7;

/** The faint cool veil a kind lays over the outdoors (null for clear skies): a still frame still reads as weather. */
export function weatherVeil(
  kind: FieldKind,
): { r: number; g: number; b: number; a: number } | null {
  return kind === 'rain'
    ? CITY_OVERCAST_RAIN
    : kind === 'snow'
      ? CITY_OVERCAST_SNOW
      : kind === 'heavy-snow'
        ? CITY_OVERCAST_HEAVY_SNOW
        : null;
}

/** How many particles a kind keeps live. */
export function particleCount(kind: FieldKind): number {
  return kind === 'rain'
    ? CITY_RAIN_PARTICLES
    : kind === 'snow'
      ? CITY_SNOW_PARTICLES
      : kind === 'heavy-snow'
        ? CITY_HEAVY_SNOW_PARTICLES
        : 0;
}

export class WeatherField {
  kind: FieldKind = 'off';
  /** Normalised screen positions, [0, 1). */
  readonly xs: Float32Array;
  readonly ys: Float32Array;
  /** Depth in [0.5, 1): nearer particles fall faster and draw larger. */
  readonly depth: Float32Array;
  private readonly phase: Float32Array;
  /** Fixed at construction: a kind never keeps more live particles than this. */
  readonly capacity: number;
  /** The share of the particle pool this field is granted (item 18): a standalone field has its whole capacity; the regional schedule sets it. */
  private limit: number;

  constructor(capacity: number = CAPACITY) {
    this.capacity = capacity;
    this.limit = capacity;
    this.xs = new Float32Array(capacity);
    this.ys = new Float32Array(capacity);
    this.depth = new Float32Array(capacity);
    this.phase = new Float32Array(capacity);
    const rng = new SeededRng(CITY_WEATHER_SEED);
    for (let i = 0; i < capacity; i++) {
      this.xs[i] = rng.next();
      this.ys[i] = rng.next();
      this.depth[i] = 0.5 + rng.next() * 0.5;
      this.phase[i] = rng.next() * Math.PI * 2;
    }
  }

  /** Live particles for the current kind: its kind's number, within its capacity and within the share the pool grants it (whole particles). */
  get count(): number {
    return Math.floor(Math.min(particleCount(this.kind), this.capacity, this.limit));
  }

  /** The pool share, a fraction of a particle at most above the live count. */
  get share(): number {
    return this.limit;
  }

  setShare(share: number): void {
    this.limit = Math.max(0, Math.min(this.capacity, share));
  }

  setKind(kind: FieldKind): void {
    this.kind = kind;
  }

  /** Advance by `dt` seconds. A no-op for no weather; callers do not call it under reduced motion. */
  update(dt: number): void {
    const n = this.count;
    if (n === 0 || !(dt > 0)) return;
    const rain = this.kind === 'rain';
    const heavy = this.kind === 'heavy-snow';
    const fall = heavy ? CITY_HEAVY_SNOW_SPEED : CITY_SNOW_SPEED;
    const sway = heavy ? CITY_HEAVY_SNOW_SWAY : CITY_SNOW_SWAY;
    for (let i = 0; i < n; i++) {
      const d = this.depth[i];
      let x = this.xs[i];
      let y = this.ys[i];
      if (rain) {
        y += CITY_RAIN_SPEED * d * dt;
        x += CITY_RAIN_DRIFT * d * dt;
      } else {
        this.phase[i] += SNOW_PHASE_RATE * d * dt;
        y += fall * d * dt;
        x += Math.cos(this.phase[i]) * sway * dt;
      }
      this.xs[i] = x - Math.floor(x);
      this.ys[i] = y - Math.floor(y);
    }
  }
}
