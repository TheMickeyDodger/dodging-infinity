/**
 * THE CITY'S SEASONS (item 18): a small, DOM-free clock that says which season the decorative city is showing.
 *
 * - AUTOMATIC cycles spring -> summer -> autumn -> winter -> spring, CITY_SEASON_INTERVAL_MS (30 minutes) of VISIBLE RUNNING time
 *   each, and starts in SUMMER (the world exactly as it has always looked). It reads no calendar, location or hemisphere.
 * - MANUAL holds the chosen season until Automatic is chosen again; Automatic then starts a FRESH full interval from that season.
 * - PAUSE freezes the automatic timer and keeps its remaining time; RESUME carries on from there.
 *
 * It advances only when `update` is called (OfficeState.update, the visible game loop), and each call is clipped to
 * CITY_CLOCK_MAX_STEP_SEC: a stalled frame or a tab that was hidden is never turned into catch-up, so at most one season boundary
 * can pass in a step and nothing replays an overdue change.
 *
 * It is decoration and nothing else: it reads no agent, Mission, assignment, approval or delivery state and writes none.
 */
import type { CityPresentationV2 } from '../../../../core/src/cityPresentation.js';
import {
  CITY_CLOCK_MAX_STEP_SEC,
  CITY_SEASON_FADE_MS,
  CITY_SEASON_INTERVAL_MS,
} from '../../constants.js';

export const SEASONS = ['spring', 'summer', 'autumn', 'winter'] as const;
export type Season = (typeof SEASONS)[number];
export type SeasonMode = 'auto' | 'manual';

/** The season that follows `season` in the cycle. */
export function nextSeason(season: Season): Season {
  return SEASONS[(SEASONS.indexOf(season) + 1) % SEASONS.length];
}

/** Plain words for a season. */
export function seasonName(season: Season): string {
  return season.charAt(0).toUpperCase() + season.slice(1);
}

/** What the renderer needs: the season shown, and, while it cross-fades, the one it is fading in from. */
export interface SeasonFrame {
  season: Season;
  /** The season fading out, or null when the change is complete (or reduced motion swapped it at once). */
  from: Season | null;
  /** How far the cross-fade has run, 0..1 (1 when `from` is null). */
  t: number;
}

/** A snapshot for the controls and for tests: plain values, no references into the engine. */
export interface SeasonState {
  mode: SeasonMode;
  season: Season;
  paused: boolean;
  /** Visible running time left in this season (automatic mode); the full interval in manual mode. */
  remainingMs: number;
}

/** The saved shape of the season (contract v2): plain values, no references into the engine. */
export type SeasonRecord = CityPresentationV2['season'];

export class SeasonEngine {
  private modeValue: SeasonMode = 'auto';
  /** Counts every change a viewer's reload would have to know about (a choice, a pause, a season change). Never per frame. */
  private revisionValue = 0;
  private seasonValue: Season = 'summer';
  private pausedValue = false;
  private remainingValue = CITY_SEASON_INTERVAL_MS;
  private fromValue: Season | null = null;
  private fadeMs = 0;

  get mode(): SeasonMode {
    return this.modeValue;
  }
  get season(): Season {
    return this.seasonValue;
  }
  get paused(): boolean {
    return this.pausedValue;
  }
  get remainingMs(): number {
    return this.remainingValue;
  }
  get revision(): number {
    return this.revisionValue;
  }

  /** The saved shape: the time left is whole milliseconds inside the contract's bound. */
  persist(): SeasonRecord {
    return {
      mode: this.modeValue,
      season: this.seasonValue,
      paused: this.pausedValue,
      remaining: Math.max(0, Math.min(CITY_SEASON_INTERVAL_MS, Math.round(this.remainingValue))),
    };
  }

  /** Take a saved season (already checked against the contract): it continues from the time left it was saved with, with no cross-fade
   *  and no catch-up for the time the view was closed. */
  restore(saved: SeasonRecord): void {
    this.modeValue = saved.mode;
    this.seasonValue = saved.season;
    this.pausedValue = saved.paused;
    this.remainingValue = Math.max(0, Math.min(CITY_SEASON_INTERVAL_MS, saved.remaining));
    this.fromValue = null;
    this.fadeMs = 0;
    this.revisionValue++;
  }

  state(): SeasonState {
    return {
      mode: this.modeValue,
      season: this.seasonValue,
      paused: this.pausedValue,
      remainingMs: this.remainingValue,
    };
  }

  /** The frame to draw, or null when the city is plainly summer (the world as it has always looked: nothing to add). */
  frame(): SeasonFrame | null {
    if (this.fromValue !== null) {
      return {
        season: this.seasonValue,
        from: this.fromValue,
        t: this.fadeMs / CITY_SEASON_FADE_MS,
      };
    }
    if (this.seasonValue === 'summer') return null;
    return { season: this.seasonValue, from: null, t: 1 };
  }

  /** Show `next`, cross-fading from the season shown now (at once under reduced motion). Returns whether the season changed. */
  private show(next: Season, reducedMotion: boolean): boolean {
    if (next === this.seasonValue) return false;
    this.revisionValue++;
    this.fromValue = reducedMotion ? null : this.seasonValue;
    this.fadeMs = 0;
    this.seasonValue = next;
    return true;
  }

  /**
   * One game-loop step of `dtSec` seconds. Returns true when the season CHANGED in this step (so the weather can re-check its
   * eligibility). The step is clipped to CITY_CLOCK_MAX_STEP_SEC; a non-positive step does nothing.
   */
  update(dtSec: number, reducedMotion: boolean): boolean {
    // reduced motion shows every change at once, so a cross-fade already running when the preference turns ON is settled to its target now
    // (the same state a change made under reduced motion has), whatever the size of this step; switching it off again restarts nothing
    if (reducedMotion && this.fromValue !== null) {
      this.fromValue = null;
      this.fadeMs = 0;
    }
    if (!(dtSec > 0)) return false;
    const dtMs = Math.min(dtSec, CITY_CLOCK_MAX_STEP_SEC) * 1000;
    if (this.fromValue !== null) {
      this.fadeMs += dtMs;
      if (this.fadeMs >= CITY_SEASON_FADE_MS) {
        this.fromValue = null;
        this.fadeMs = 0;
      }
    }
    if (this.modeValue !== 'auto' || this.pausedValue) return false;
    this.remainingValue -= dtMs;
    if (this.remainingValue > 0) return false;
    // one boundary per step at most (the step is clipped); the new season gets a fresh full interval, no overdue replay
    this.remainingValue = CITY_SEASON_INTERVAL_MS;
    return this.show(nextSeason(this.seasonValue), reducedMotion);
  }

  /** Hold `season` until Automatic is chosen again. Returns whether the season shown changed. */
  setManual(season: Season, reducedMotion: boolean): boolean {
    this.revisionValue++;
    this.modeValue = 'manual';
    this.remainingValue = CITY_SEASON_INTERVAL_MS;
    return this.show(season, reducedMotion);
  }

  /** Back to Automatic: a FRESH full interval from the season shown now. */
  setAutomatic(): void {
    this.revisionValue++;
    this.modeValue = 'auto';
    this.remainingValue = CITY_SEASON_INTERVAL_MS;
  }

  /** Freeze the automatic timer; its remaining time is kept. */
  pause(): void {
    if (!this.pausedValue) this.revisionValue++;
    this.pausedValue = true;
  }

  /** Carry the automatic timer on from where it stopped. */
  resume(): void {
    if (this.pausedValue) this.revisionValue++;
    this.pausedValue = false;
  }
}

/** "12 min left" / "less than a minute left" for the plain-English status line. */
export function minutesLeft(remainingMs: number): string {
  const minutes = Math.ceil(remainingMs / 60_000);
  return minutes <= 1 ? 'less than a minute left' : `${minutes} min left`;
}

/** The words for the season control's status line (the scenarios of the design note, D13). */
export function seasonStatus(state: SeasonState): string {
  const name = seasonName(state.season);
  if (state.mode === 'manual') return `Held on ${name} until you choose Automatic.`;
  if (state.paused)
    return `Paused on ${name}, ${minutesLeft(state.remainingMs)}. Resume to carry on.`;
  return `Automatic · ${name} · ${minutesLeft(state.remainingMs)}`;
}
