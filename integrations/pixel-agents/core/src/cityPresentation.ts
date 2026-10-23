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

// ── CONTRACT v2 (item 18): seasons and regional weather, with their bounded schedule state ──────────────────────────────────────

/**
 * v2 holds what v1 held (lighting, traffic) plus the SEASON and the REGIONAL WEATHER the viewer sees, so that a reload continues where
 * the city was. It is still local decoration and nothing else: a mode, a count, a time left and a seed, never a position, a Mission, an
 * agent, a model, an observation or anything canonical, and no value in it comes from a location, a calendar or a weather service.
 *
 * - season: Automatic or held (Manual), which season, whether the automatic timer is paused, and the visible running time left in it.
 * - weather: Automatic or one manual kind for every region, the schedule's seed, and EXACTLY one small state per region slot (kind,
 *   how many automatic draws it has made, its consecutive non-clear draws, the running time left before its next draw).
 *
 * Every number is an integer inside a pinned bound and the arrays have an exact length. In
 * Automatic weather each region's wet streak also agrees with its kind (clear: 0, wet: 1 to the pinned maximum), which every state the
 * product itself produces satisfies; a record that breaks it is refused whole like any other. A record that passes can still name a kind
 * that is not ELIGIBLE for the region's class and season (a hand-edited or foreign file): the webview checks every restored automatic region
 * against the layout's real classes and re-draws such a region deterministically (see RegionalWeather.restore).
 * Time left is VISIBLE RUNNING time only: a reload restores it as it was saved and nothing is caught up for the time the view was closed.
 */
export const CITY_PRESENTATION_V2_VERSION = 2;
/**
 * Hard byte ceiling of the STORED v2 record (the compact form below, as written to disk): the same 256 bytes as the v1 record, because
 * the saved city choices stay within their documented 256-byte budget. The largest admissible stored record is 160 bytes.
 */
export const CITY_PRESENTATION_V2_MAX_BYTES = 256;
/** Region slots: exactly this many entries in `weather.regions`. */
export const CITY_PRESENTATION_REGION_SLOTS = 5;
/** Largest time left in a season, in ms (30 minutes), and in a region's weather interval, in ms (5 minutes). */
export const CITY_PRESENTATION_SEASON_REMAINING_MAX_MS = 1_800_000;
export const CITY_PRESENTATION_REGION_REMAINING_MAX_MS = 300_000;
/** Largest consecutive non-clear automatic draw count a region may carry (the bound after which the next draw is clear). */
export const CITY_PRESENTATION_WET_STREAK_MAX = 2;
/** Largest automatic draw index of a region (the schedule wraps to 0 after it), and the largest seed (an unsigned 32-bit integer). */
export const CITY_PRESENTATION_STEP_MAX = 999_999;
export const CITY_PRESENTATION_SEED_MAX = 4_294_967_295;
/** The default seed of the schedule (a new city, and the seed a v1 record migrates to). */
export const CITY_PRESENTATION_DEFAULT_SEED = 0x51a7;

export const CITY_SEASON_NAMES = ['spring', 'summer', 'autumn', 'winter'] as const;
export type CitySeasonName = (typeof CITY_SEASON_NAMES)[number];
export const CITY_SEASON_MODES = ['auto', 'manual'] as const;
export type CitySeasonMode = (typeof CITY_SEASON_MODES)[number];
/** The kinds of weather a region can show. */
export const CITY_WEATHER_KINDS = ['clear', 'rain', 'light-snow', 'heavy-snow'] as const;
export type CityWeatherKind = (typeof CITY_WEATHER_KINDS)[number];
/** v2 weather modes: Automatic regional weather, or one manual kind for every region. */
export const CITY_WEATHER_V2_MODES = ['auto', ...CITY_WEATHER_KINDS] as const;
export type CityWeatherV2Mode = (typeof CITY_WEATHER_V2_MODES)[number];

export interface CityRegionState {
  kind: CityWeatherKind;
  /** How many automatic draws this region has made (the next draw's index). */
  step: number;
  /** Consecutive non-clear automatic draws just made. */
  wet: number;
  /** Visible running time left before this region's next automatic draw, in ms. */
  remaining: number;
}

export interface CityPresentationV2 {
  v: typeof CITY_PRESENTATION_V2_VERSION;
  lighting: CityLighting;
  traffic: CityTraffic;
  season: {
    mode: CitySeasonMode;
    season: CitySeasonName;
    paused: boolean;
    remaining: number;
  };
  weather: {
    mode: CityWeatherV2Mode;
    seed: number;
    regions: CityRegionState[];
  };
}

const V2_KEYS = ['v', 'lighting', 'traffic', 'season', 'weather'];
const SEASON_KEYS = ['mode', 'season', 'paused', 'remaining'];
const WEATHER_KEYS = ['mode', 'seed', 'regions'];
const REGION_KEYS = ['kind', 'step', 'wet', 'remaining'];
const sameKeys = (o: Record<string, unknown>, keys: readonly string[]): boolean => {
  const have = Object.keys(o);
  return have.length === keys.length && keys.every((k) => have.includes(k));
};
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const isIntIn = (v: unknown, max: number): boolean =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= max;

/** What a new city is: Automatic seasons from summer with a full interval, Automatic regional weather, day, normal traffic. */
export function defaultCityPresentationV2(): CityPresentationV2 {
  return {
    v: CITY_PRESENTATION_V2_VERSION,
    lighting: 'day',
    traffic: 'normal',
    season: {
      mode: 'auto',
      season: 'summer',
      paused: false,
      remaining: CITY_PRESENTATION_SEASON_REMAINING_MAX_MS,
    },
    weather: {
      mode: 'auto',
      seed: CITY_PRESENTATION_DEFAULT_SEED,
      regions: Array.from({ length: CITY_PRESENTATION_REGION_SLOTS }, () => ({
        kind: 'clear' as const,
        step: 0,
        wet: 0,
        remaining: 0,
      })),
    },
  };
}

/** null when the value is an admissible v2 record, else the reason it is refused. Strict at every level. */
export function checkCityPresentationV2(state: unknown): string | null {
  if (!isRecord(state)) return 'preferences are not an object';
  if (!sameKeys(state, V2_KEYS)) return 'preference keys differ from the contract';
  if (state.v !== CITY_PRESENTATION_V2_VERSION) return 'preference version unsupported';
  if (!oneOf(CITY_LIGHTING_MODES, state.lighting)) return 'lighting mode unknown';
  if (!oneOf(CITY_TRAFFIC_MODES, state.traffic)) return 'traffic mode unknown';
  const season = state.season;
  if (!isRecord(season) || !sameKeys(season, SEASON_KEYS))
    return 'season keys differ from the contract';
  if (!oneOf(CITY_SEASON_MODES, season.mode)) return 'season mode unknown';
  if (!oneOf(CITY_SEASON_NAMES, season.season)) return 'season unknown';
  if (typeof season.paused !== 'boolean') return 'season pause flag is not a boolean';
  if (!isIntIn(season.remaining, CITY_PRESENTATION_SEASON_REMAINING_MAX_MS))
    return 'season time left out of range';
  const weather = state.weather;
  if (!isRecord(weather) || !sameKeys(weather, WEATHER_KEYS))
    return 'weather keys differ from the contract';
  if (!oneOf(CITY_WEATHER_V2_MODES, weather.mode)) return 'weather mode unknown';
  if (!isIntIn(weather.seed, CITY_PRESENTATION_SEED_MAX)) return 'weather seed out of range';
  const regions = weather.regions;
  if (!Array.isArray(regions) || regions.length !== CITY_PRESENTATION_REGION_SLOTS)
    return `weather needs exactly ${CITY_PRESENTATION_REGION_SLOTS} regions`;
  for (const region of regions as unknown[]) {
    if (!isRecord(region) || !sameKeys(region, REGION_KEYS))
      return 'region keys differ from the contract';
    if (!oneOf(CITY_WEATHER_KINDS, region.kind)) return 'region weather kind unknown';
    if (!isIntIn(region.step, CITY_PRESENTATION_STEP_MAX)) return 'region draw index out of range';
    if (!isIntIn(region.wet, CITY_PRESENTATION_WET_STREAK_MAX))
      return 'region wet streak out of range';
    if (!isIntIn(region.remaining, CITY_PRESENTATION_REGION_REMAINING_MAX_MS))
      return 'region time left out of range';
    // In AUTOMATIC weather every region's kind came from an automatic draw, so the streak and the kind agree: a clear region has just
    // drawn clear (streak 0) and a wet one has made at least one wet draw (a manual weather leaves the streak as it was, so the
    // relationship is asserted only while the mode is automatic).
    if (weather.mode === 'auto' && (region.kind === 'clear') !== (region.wet === 0))
      return 'region wet streak disagrees with its kind';
  }
  // No byte test here: this named form is what the webview and the wire carry, and a closed shape (fixed keys, enumerations, bounded
  // integers, exactly five regions) cannot grow. The byte ceiling belongs to the STORED form (encodeStoredCityPresentationV2).
  return null;
}

/** The v2 reading of a v1 weather choice: an explicit, MANUAL choice (off to Clear, rain to Rain, snow to Light snow). */
export function migratedWeatherMode(weather: CityWeather): CityWeatherKind {
  return weather === 'rain' ? 'rain' : weather === 'snow' ? 'light-snow' : 'clear';
}

export type CityMigration =
  { ok: true; state: CityPresentationV2; from: 1 | 2 } | { ok: false; reason: string };

/**
 * THE ONE MIGRATION (used by the server when it reads a saved record and by the webview when it applies one). A valid v2 record is
 * returned as it is. A valid v1 record becomes v2: lighting and traffic are kept, its weather becomes the same MANUAL choice, and the
 * seasons start Automatic from summer with a full interval (a new city's schedule state). Anything else is refused with a reason and
 * nothing is partly kept: the caller applies the defaults and touches no other store.
 */
export function migrateCityPresentation(raw: unknown): CityMigration {
  if (isRecord(raw) && raw.v === CITY_PRESENTATION_V2_VERSION) {
    const reason = checkCityPresentationV2(raw);
    return reason === null
      ? { ok: true, state: raw as unknown as CityPresentationV2, from: 2 }
      : { ok: false, reason };
  }
  const v1 = checkCityPresentation(raw);
  if (v1 !== null) return { ok: false, reason: v1 };
  const old = raw as unknown as CityPresentation;
  const fresh = defaultCityPresentationV2();
  const migrated: CityPresentationV2 = {
    ...fresh,
    lighting: old.lighting,
    traffic: old.traffic,
    weather: { ...fresh.weather, mode: migratedWeatherMode(old.weather) },
  };
  const reason = checkCityPresentationV2(migrated);
  return reason === null ? { ok: true, state: migrated, from: 1 } : { ok: false, reason };
}

/**
 * THE STORED FORM of a v2 record: what the server writes to disk, so that the saved city choices stay inside their 256-byte budget with
 * every field kept. The named record above is packed with small integer CODES and per-region arrays:
 *
 *   {"v":2,"l":lighting,"t":traffic,"s":[mode,season,paused,remaining],"w":[mode,seed,[[kind,step,wet,remaining] x 5]]}
 *
 * A code is the index of the value in its ordered list (CITY_LIGHTING_MODES, CITY_TRAFFIC_MODES, CITY_SEASON_MODES, CITY_SEASON_NAMES,
 * CITY_WEATHER_V2_MODES, CITY_WEATHER_KINDS); the lists are pinned by exact-value tests and by golden stored records, so reordering one
 * cannot silently change what a saved file means. `paused` is 0 or 1. Unpacking rebuilds the named record and runs the same
 * `checkCityPresentationV2`, so there is one definition of every range and invariant.
 */
export type StoredCityRegion = [kind: number, step: number, wet: number, remaining: number];
export interface StoredCityPresentationV2 {
  v: typeof CITY_PRESENTATION_V2_VERSION;
  l: number;
  t: number;
  s: [mode: number, season: number, paused: number, remaining: number];
  w: [mode: number, seed: number, regions: StoredCityRegion[]];
}

const STORED_KEYS = ['v', 'l', 't', 's', 'w'];

export function packCityPresentationV2(state: CityPresentationV2): StoredCityPresentationV2 {
  return {
    v: CITY_PRESENTATION_V2_VERSION,
    l: CITY_LIGHTING_MODES.indexOf(state.lighting),
    t: CITY_TRAFFIC_MODES.indexOf(state.traffic),
    s: [
      CITY_SEASON_MODES.indexOf(state.season.mode),
      CITY_SEASON_NAMES.indexOf(state.season.season),
      state.season.paused ? 1 : 0,
      state.season.remaining,
    ],
    w: [
      CITY_WEATHER_V2_MODES.indexOf(state.weather.mode),
      state.weather.seed,
      state.weather.regions.map((r): StoredCityRegion => [
        CITY_WEATHER_KINDS.indexOf(r.kind),
        r.step,
        r.wet,
        r.remaining,
      ]),
    ],
  };
}

/** The compact text to write, or the reason the state is refused. `maxBytes` is the pinned ceiling (a parameter so the boundary is testable). */
export function encodeStoredCityPresentationV2(
  state: unknown,
  maxBytes: number = CITY_PRESENTATION_V2_MAX_BYTES,
): { ok: true; text: string } | { ok: false; reason: string } {
  const reason = checkCityPresentationV2(state);
  if (reason !== null) return { ok: false, reason };
  const text = JSON.stringify(packCityPresentationV2(state as CityPresentationV2));
  if (text.length > maxBytes) return { ok: false, reason: `preferences over ${maxBytes} bytes` };
  return { ok: true, text };
}

const codeOf = (list: readonly string[], v: unknown): string | undefined =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 && v < list.length ? list[v] : undefined;

/** The named v2 record from a stored one (strict at every level), or the reason it is refused. */
export function unpackCityPresentationV2(raw: unknown): CityMigration {
  const refuse = (reason: string): CityMigration => ({ ok: false, reason });
  if (!isRecord(raw) || !sameKeys(raw, STORED_KEYS))
    return refuse('stored preference keys differ from the contract');
  if (raw.v !== CITY_PRESENTATION_V2_VERSION) return refuse('preference version unsupported');
  const { l, t, s, w } = raw;
  if (!Array.isArray(s) || s.length !== 4) return refuse('stored season needs exactly 4 fields');
  if (!Array.isArray(w) || w.length !== 3 || !Array.isArray(w[2]))
    return refuse('stored weather needs a mode, a seed and a region list');
  const lighting = codeOf(CITY_LIGHTING_MODES, l);
  if (lighting === undefined) return refuse('lighting mode unknown');
  const traffic = codeOf(CITY_TRAFFIC_MODES, t);
  if (traffic === undefined) return refuse('traffic mode unknown');
  const seasonMode = codeOf(CITY_SEASON_MODES, s[0]);
  if (seasonMode === undefined) return refuse('season mode unknown');
  const season = codeOf(CITY_SEASON_NAMES, s[1]);
  if (season === undefined) return refuse('season unknown');
  if (s[2] !== 0 && s[2] !== 1) return refuse('season pause flag is not 0 or 1');
  const weatherMode = codeOf(CITY_WEATHER_V2_MODES, w[0]);
  if (weatherMode === undefined) return refuse('weather mode unknown');
  const regions: unknown[] = [];
  for (const region of w[2] as unknown[]) {
    if (!Array.isArray(region) || region.length !== 4)
      return refuse('stored region needs exactly 4 fields');
    const kind = codeOf(CITY_WEATHER_KINDS, region[0]);
    if (kind === undefined) return refuse('region weather kind unknown');
    regions.push({ kind, step: region[1], wet: region[2], remaining: region[3] });
  }
  const state = {
    v: CITY_PRESENTATION_V2_VERSION,
    lighting,
    traffic,
    season: { mode: seasonMode, season, paused: s[2] === 1, remaining: s[3] },
    weather: { mode: weatherMode, seed: w[1], regions },
  };
  const reason = checkCityPresentationV2(state);
  return reason === null
    ? { ok: true, state: state as unknown as CityPresentationV2, from: 2 }
    : refuse(reason);
}

/**
 * Read a record FROM STORAGE (the server, on load): a stored v2 record is unpacked, a v1 record is migrated by THE ONE MIGRATION above,
 * anything else is refused whole with a reason. The result is the named record the wire and the webview use.
 */
export function readStoredCityPresentation(raw: unknown): CityMigration {
  return isRecord(raw) && raw.v === CITY_PRESENTATION_V2_VERSION
    ? unpackCityPresentationV2(raw)
    : migrateCityPresentation(raw);
}
