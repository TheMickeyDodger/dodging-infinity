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

const CAPACITY = Math.max(CITY_RAIN_PARTICLES, CITY_SNOW_PARTICLES);
/** Sway phase speed of a snowflake, in radians per second at full depth. */
export const SNOW_PHASE_RATE = 1.7;

/** The faint cool veil a kind lays over the outdoors (null for clear skies): a still frame still reads as weather. */
export function weatherVeil(
  kind: CityWeather,
): { r: number; g: number; b: number; a: number } | null {
  return kind === 'rain' ? CITY_OVERCAST_RAIN : kind === 'snow' ? CITY_OVERCAST_SNOW : null;
}

/** How many particles a kind keeps live. */
export function particleCount(kind: CityWeather): number {
  return kind === 'rain' ? CITY_RAIN_PARTICLES : kind === 'snow' ? CITY_SNOW_PARTICLES : 0;
}

export class WeatherField {
  kind: CityWeather = 'off';
  /** Normalised screen positions, [0, 1). */
  readonly xs = new Float32Array(CAPACITY);
  readonly ys = new Float32Array(CAPACITY);
  /** Depth in [0.5, 1): nearer particles fall faster and draw larger. */
  readonly depth = new Float32Array(CAPACITY);
  private readonly phase = new Float32Array(CAPACITY);

  constructor() {
    const rng = new SeededRng(CITY_WEATHER_SEED);
    for (let i = 0; i < CAPACITY; i++) {
      this.xs[i] = rng.next();
      this.ys[i] = rng.next();
      this.depth[i] = 0.5 + rng.next() * 0.5;
      this.phase[i] = rng.next() * Math.PI * 2;
    }
  }

  /** Live particles for the current kind. */
  get count(): number {
    return particleCount(this.kind);
  }

  setKind(kind: CityWeather): void {
    this.kind = kind;
  }

  /** Advance by `dt` seconds. A no-op for no weather; callers do not call it under reduced motion. */
  update(dt: number): void {
    const n = this.count;
    if (n === 0 || !(dt > 0)) return;
    const rain = this.kind === 'rain';
    for (let i = 0; i < n; i++) {
      const d = this.depth[i];
      let x = this.xs[i];
      let y = this.ys[i];
      if (rain) {
        y += CITY_RAIN_SPEED * d * dt;
        x += CITY_RAIN_DRIFT * d * dt;
      } else {
        this.phase[i] += SNOW_PHASE_RATE * d * dt;
        y += CITY_SNOW_SPEED * d * dt;
        x += Math.cos(this.phase[i]) * CITY_SNOW_SWAY * dt;
      }
      this.xs[i] = x - Math.floor(x);
      this.ys[i] = y - Math.floor(y);
    }
  }
}
