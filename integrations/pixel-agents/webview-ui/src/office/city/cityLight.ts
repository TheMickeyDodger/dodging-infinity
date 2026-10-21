/**
 * LIGHTING MATH (living city 6–9): which stage of day the decorative city shows, as one number, and
 * the colour of the darkness for it. DOM-free and pure: the level is a function of the chosen mode,
 * the clock it is handed and the reduced-motion preference, so it is tested as is.
 *
 * LEVEL: 0 = day (the city exactly as it always looked), 0.5 = dusk, 1 = night.
 *
 * TIMING, stated plainly (the Settings text says the same):
 * - MANUAL (day, dusk, night): a still stage the viewer chose. Nothing advances.
 * - CLOCK: follows THIS computer's local time of day while the view is open. It is a function of the
 *   clock, not a simulation: nothing is simulated while the view is closed, asleep or rebooted, and
 *   opening it later simply shows whatever stage the clock is at.
 * There is no accelerated day cycle and no stored time: nothing here is persisted but the chosen mode.
 */
import type { CityLighting } from '../../../../core/src/cityPresentation.js';
import {
  CITY_CLOCK_DAWN_START_H,
  CITY_CLOCK_DAY_START_H,
  CITY_CLOCK_DUSK_START_H,
  CITY_CLOCK_NIGHT_START_H,
  CITY_LIGHT_FADE_SEC,
  CITY_LIGHT_LEVEL_DUSK,
  CITY_LIGHT_LEVEL_NIGHT,
  CITY_LIGHT_STAGE_DAY,
  CITY_LIGHT_STAGE_DUSK,
  CITY_LIGHT_STAGE_NIGHT,
} from '../../constants.js';

export type LightStage = 'day' | 'dusk' | 'night';

export interface DarknessColor {
  r: number;
  g: number;
  b: number;
  /** Opacity of the darkness over the outdoors at this level. */
  a: number;
}

/** Local hours since midnight (0 <= h < 24) for a millisecond timestamp, in THIS computer's timezone. */
export function localHours(nowMs: number): number {
  const d = new Date(nowMs);
  return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600;
}

/** The darkness level the local clock shows at `hours`: night, a dawn ramp, day, a dusk ramp. */
export function clockLevel(hours: number): number {
  const h = ((hours % 24) + 24) % 24;
  if (h >= CITY_CLOCK_NIGHT_START_H || h < CITY_CLOCK_DAWN_START_H) return CITY_LIGHT_LEVEL_NIGHT;
  if (h >= CITY_CLOCK_DAY_START_H && h < CITY_CLOCK_DUSK_START_H) return 0;
  if (h >= CITY_CLOCK_DUSK_START_H) {
    return (h - CITY_CLOCK_DUSK_START_H) / (CITY_CLOCK_NIGHT_START_H - CITY_CLOCK_DUSK_START_H);
  }
  return 1 - (h - CITY_CLOCK_DAWN_START_H) / (CITY_CLOCK_DAY_START_H - CITY_CLOCK_DAWN_START_H);
}

export function stageLevel(stage: LightStage): number {
  return stage === 'day' ? 0 : stage === 'dusk' ? CITY_LIGHT_LEVEL_DUSK : CITY_LIGHT_LEVEL_NIGHT;
}

/** The named stage a level is closest to (the words and the reduced-motion snap use this). */
export function nearestStage(level: number): LightStage {
  if (level < CITY_LIGHT_LEVEL_DUSK / 2) return 'day';
  if (level < (CITY_LIGHT_LEVEL_DUSK + CITY_LIGHT_LEVEL_NIGHT) / 2) return 'dusk';
  return 'night';
}

/** The level the chosen mode asks for at `nowMs`. Under reduced motion the clock mode shows the nearest
 *  STILL stage instead of a gradual ramp. */
export function targetLevel(mode: CityLighting, nowMs: number, reducedMotion: boolean): number {
  switch (mode) {
    case 'day':
      return 0;
    case 'dusk':
      return CITY_LIGHT_LEVEL_DUSK;
    case 'night':
      return CITY_LIGHT_LEVEL_NIGHT;
    case 'clock': {
      const level = clockLevel(localHours(nowMs));
      return reducedMotion ? stageLevel(nearestStage(level)) : level;
    }
  }
}

/** Move the shown level toward the target: instantly under reduced motion, else over CITY_LIGHT_FADE_SEC
 *  for a full day-to-night change. */
export function approachLevel(
  current: number,
  target: number,
  dt: number,
  reducedMotion: boolean,
): number {
  if (reducedMotion) return target;
  if (!(dt > 0)) return current;
  const step = dt / CITY_LIGHT_FADE_SEC;
  if (Math.abs(target - current) <= step) return target;
  return current + Math.sign(target - current) * step;
}

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
const mix = (from: DarknessColor, to: DarknessColor, t: number): DarknessColor => ({
  r: Math.round(lerp(from.r, to.r, t)),
  g: Math.round(lerp(from.g, to.g, t)),
  b: Math.round(lerp(from.b, to.b, t)),
  a: Math.round(lerp(from.a, to.a, t) * 1000) / 1000,
});

/** The darkness colour at a level: day to dusk to night, interpolated between the stage colours. */
export function levelColor(level: number): DarknessColor {
  const l = Math.min(CITY_LIGHT_LEVEL_NIGHT, Math.max(0, level));
  if (l <= CITY_LIGHT_LEVEL_DUSK)
    return mix(CITY_LIGHT_STAGE_DAY, CITY_LIGHT_STAGE_DUSK, l / CITY_LIGHT_LEVEL_DUSK);
  return mix(
    CITY_LIGHT_STAGE_DUSK,
    CITY_LIGHT_STAGE_NIGHT,
    (l - CITY_LIGHT_LEVEL_DUSK) / (CITY_LIGHT_LEVEL_NIGHT - CITY_LIGHT_LEVEL_DUSK),
  );
}

/** The words for the current lighting, for the control's live status (a cue beyond the picture). */
export function lightingStatus(mode: CityLighting, nowMs: number): string {
  if (mode === 'clock') {
    // the hour and minute come from the date itself: deriving the minute from fractional hours loses one to float error
    const d = new Date(nowMs);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `Following this computer's clock (${hh}:${mm}): ${nearestStage(clockLevel(localHours(nowMs)))}`;
  }
  return mode === 'day'
    ? 'Day: the city as it always looked'
    : `${mode === 'dusk' ? 'Dusk' : 'Night'}: a still stage you chose`;
}
