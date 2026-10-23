/**
 * THE CITY'S DECORATIVE EFFECTS STATE (living city 6–9, item 18): the viewer's presentation choices, the lighting level currently
 * SHOWN, the SEASON engine and the REGIONAL WEATHER. One instance lives in OfficeState; the renderer reads it.
 *
 * It is local decoration and nothing else. It reads no agent, Mission, assignment, approval or delivery
 * state and writes none; real agents only appear to the renderer as positions that get a light pool.
 *
 * Advancing it is the game loop's job (OfficeState.update): the loop's hidden guard means nothing here
 * advances while the view is hidden, and `catchUp` does not exist: lighting is a function of the clock
 * (clock mode) or a still stage, and the seasons and weather clocks take only the (clipped) steps they are handed.
 *
 * SAVING (contract v2, core/src/cityPresentation.ts): `record()` is everything a reload needs (lighting, traffic, the season and the weather
 * schedule with its time left) and `loadRecord()` takes one back, with no catch-up. `persistRevision` counts the changes worth saving
 * (a choice, a season change, an automatic weather draw) so the saver never has to look at a frame. A v1 record is migrated to v2 by the
 * shared `migrateCityPresentation` before it reaches this class.
 */
import type {
  CityPresentation,
  CityPresentationV2,
  CityWeather,
} from '../../../../core/src/cityPresentation.js';
import {
  CITY_PRESENTATION_V2_VERSION,
  DEFAULT_CITY_PRESENTATION,
  migratedWeatherMode,
} from '../../../../core/src/cityPresentation.js';
import { approachLevel, lightingStatus, targetLevel } from './cityLight.js';
import type { RegionMap } from './cityRegions.js';
import { regionName } from './cityRegions.js';
import type { RegionKindEntry, WeatherKind, WeatherMode } from './cityRegionWeather.js';
import { RegionalWeather } from './cityRegionWeather.js';
import type { Season, SeasonState } from './citySeason.js';
import { SeasonEngine } from './citySeason.js';

/** What the City panel shows of the seasons and the weather: plain values, copied out of the engines. */
export interface CityAtmosphere {
  season: SeasonState;
  weatherMode: WeatherMode;
  regions: RegionKindEntry[];
}

/** The D12 reading of a legacy weather choice: an explicit, manual choice (off -> Clear, rain -> Rain, snow -> Light snow): the core's
 *  one mapping, so a caller that still sets the v1 `weather` field gets exactly what a migrated v1 record gets. */
export function legacyWeatherMode(weather: CityWeather): WeatherKind {
  return migratedWeatherMode(weather);
}

export class CityEffects {
  private prefs: CityPresentation = { ...DEFAULT_CITY_PRESENTATION };
  /** The darkness level shown now (0 day .. 1 night). */
  level = 0;
  private advancedMs = 0;
  readonly seasons = new SeasonEngine();
  readonly regional = new RegionalWeather();

  get presentation(): Readonly<CityPresentation> {
    return this.prefs;
  }

  /**
   * Take the viewer's choices. Under reduced motion the shown level jumps to its target at once. A CHANGED legacy `weather` field
   * is taken as a manual weather choice (so a caller that sets `weather` still gets that weather everywhere); the season and the
   * new weather modes have their own entry points below.
   */
  setPresentation(next: CityPresentation, nowMs: number, reducedMotion: boolean): void {
    const weatherChanged = next.weather !== this.prefs.weather;
    this.prefs = { ...next };
    if (weatherChanged) this.regional.setMode(legacyWeatherMode(next.weather), reducedMotion);
    if (reducedMotion) this.level = targetLevel(next.lighting, nowMs, true);
  }

  /** Changes worth saving so far (a choice, a pause, a season change, a weather mode or draw). Monotonic; never advances per frame. */
  get persistRevision(): number {
    return this.seasons.revision + this.regional.revision;
  }

  /** Whether a saved clock is SET to run (an Automatic, unpaused season, or Automatic weather): its time left moves while the view steps. */
  persistsRunning(): boolean {
    return (this.seasons.mode === 'auto' && !this.seasons.paused) || this.regional.mode === 'auto';
  }

  /**
   * The running time, in ms, that a saved clock has ACTUALLY ADVANCED through game-loop steps. Monotonic; it moves only inside `update`, so
   * it stands still while the view is hidden or the loop is not stepping, however long the wall clock runs. The save gate compares it with
   * its value at the last save: an unchanged record is never rewritten.
   */
  get runningAdvanceMs(): number {
    return this.advancedMs;
  }

  /** Everything a reload needs, as plain values (contract v2). */
  record(): CityPresentationV2 {
    return {
      v: CITY_PRESENTATION_V2_VERSION,
      lighting: this.prefs.lighting,
      traffic: this.prefs.traffic,
      season: this.seasons.persist(),
      weather: this.regional.persist(),
    };
  }

  /**
   * Take a saved v2 record (already checked against the contract, migrated if it was v1): the viewer's lighting and traffic, the season
   * and the weather schedule continue from the time left they were saved with. NOTHING is caught up for the time the view was closed,
   * and nothing overdue is replayed. The legacy `weather` field of the choices is inert from here on.
   */
  loadRecord(record: CityPresentationV2, nowMs: number, reducedMotion: boolean): void {
    this.prefs = { v: 1, lighting: record.lighting, weather: 'off', traffic: record.traffic };
    this.seasons.restore(record.season);
    this.regional.restore(record.weather, record.season.season);
    if (reducedMotion) this.level = targetLevel(record.lighting, nowMs, true);
  }

  /** Hold `season` until Automatic is chosen again. */
  chooseSeason(season: Season, reducedMotion: boolean): void {
    if (this.seasons.setManual(season, reducedMotion))
      this.regional.setSeason(season, reducedMotion);
  }

  /** Back to Automatic seasons: a fresh full interval from the season shown now. */
  chooseAutomaticSeasons(): void {
    this.seasons.setAutomatic();
  }

  pauseSeasons(): void {
    this.seasons.pause();
  }

  resumeSeasons(): void {
    this.seasons.resume();
  }

  /** Choose Automatic regional weather, or one manual kind for every region. */
  chooseWeather(mode: WeatherMode, reducedMotion: boolean): void {
    this.regional.setMode(mode, reducedMotion);
  }

  /** One game-loop step. The level follows its target; the season clock, the weather schedule and the particle fields advance. */
  update(dt: number, nowMs: number, reducedMotion: boolean): void {
    if (dt > 0 && this.persistsRunning()) this.advancedMs += dt * 1000;
    this.level = approachLevel(
      this.level,
      targetLevel(this.prefs.lighting, nowMs, reducedMotion),
      dt,
      reducedMotion,
    );
    if (this.seasons.update(dt, reducedMotion))
      this.regional.setSeason(this.seasons.season, reducedMotion);
    this.regional.update(dt, reducedMotion);
  }

  /** The words for the current lighting choice (for the control's live status). */
  lightingStatus(nowMs: number): string {
    return lightingStatus(this.prefs.lighting, nowMs);
  }

  /** The season and the weather as the controls describe them, for the regions of `map`. */
  atmosphere(map: RegionMap): CityAtmosphere {
    const regions: RegionKindEntry[] = [];
    for (let slot = 0; slot < map.slots; slot++) {
      regions.push({ slot, name: regionName(map, slot), kind: this.regional.kindAt(slot) });
    }
    return { season: this.seasons.state(), weatherMode: this.regional.mode, regions };
  }
}
