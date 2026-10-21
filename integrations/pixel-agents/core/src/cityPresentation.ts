/**
 * THE CITY PRESENTATION PREFERENCES (living city 6–9) — the ONE definition of what the DI office may
 * save about how its decorative city LOOKS and MOVES, shared by the server (checked before anything
 * is written, and again when read) and the webview (checked again before anything is applied).
 *
 * It holds three choices and nothing else:
 * - lighting: how bright the city is (a still stage, or the viewer's own clock);
 * - weather: none, rain or snow, drawn over the outdoors only;
 * - traffic: how much decorative road traffic moves.
 *
 * What it never holds: a position, a time, a count, a seed, a Mission, an agent, a model, an
 * observation or anything canonical. Every one of these is local DECORATION: choosing one cannot change
 * what an agent is doing, who is assigned, or anything approved or delivered, and no value here is
 * derived from a location, a weather service or the backend's health.
 */

export const CITY_PRESENTATION_VERSION = 1;
/** Hard byte ceiling of a saved record (its compact JSON serialization). */
export const CITY_PRESENTATION_MAX_BYTES = 256;

/**
 * Lighting. `day` is the city exactly as it was before lighting existed (no overlay, no cost) and is the
 * default. `dusk` and `night` are still stages. `clock` follows THIS computer's local time of day while
 * the view is open: a function of the clock, not a simulation, so it advances nothing while the view is
 * closed or asleep and simply shows the right stage when it is opened again.
 */
export const CITY_LIGHTING_MODES = ['day', 'dusk', 'night', 'clock'] as const;
export type CityLighting = (typeof CITY_LIGHTING_MODES)[number];

/** Weather: local decoration over the outdoors; never a forecast or a location. */
export const CITY_WEATHER_MODES = ['off', 'rain', 'snow'] as const;
export type CityWeather = (typeof CITY_WEATHER_MODES)[number];

/** Traffic: how many decorative vehicles are allowed on the roads (a finite cap per level). */
export const CITY_TRAFFIC_MODES = ['off', 'low', 'normal'] as const;
export type CityTraffic = (typeof CITY_TRAFFIC_MODES)[number];

export interface CityPresentation {
  v: typeof CITY_PRESENTATION_VERSION;
  lighting: CityLighting;
  weather: CityWeather;
  traffic: CityTraffic;
}

/** What a viewer with no saved choice sees: the city as it has always looked, with its traffic. */
export const DEFAULT_CITY_PRESENTATION: Readonly<CityPresentation> = Object.freeze({
  v: CITY_PRESENTATION_VERSION,
  lighting: 'day',
  weather: 'off',
  traffic: 'normal',
});

const KEYS = ['v', 'lighting', 'weather', 'traffic'];
const oneOf = (list: readonly string[], v: unknown): boolean =>
  typeof v === 'string' && list.includes(v);

/** The byte length of the compact JSON serialization (the record is pure ASCII once checked). */
export function cityPresentationBytes(state: unknown): number {
  try {
    return JSON.stringify(state).length;
  } catch {
    return Infinity;
  }
}

/** null when the value is an admissible record, else the reason it is refused. */
export function checkCityPresentation(state: unknown): string | null {
  if (typeof state !== 'object' || state === null || Array.isArray(state))
    return 'preferences are not an object';
  const o = state as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== KEYS.length || !KEYS.every((k) => keys.includes(k)))
    return 'preference keys differ from the contract';
  if (o.v !== CITY_PRESENTATION_VERSION) return 'preference version unsupported';
  if (!oneOf(CITY_LIGHTING_MODES, o.lighting)) return 'lighting mode unknown';
  if (!oneOf(CITY_WEATHER_MODES, o.weather)) return 'weather mode unknown';
  if (!oneOf(CITY_TRAFFIC_MODES, o.traffic)) return 'traffic mode unknown';
  if (cityPresentationBytes(state) > CITY_PRESENTATION_MAX_BYTES)
    return `preferences over ${CITY_PRESENTATION_MAX_BYTES} bytes`;
  return null;
}
