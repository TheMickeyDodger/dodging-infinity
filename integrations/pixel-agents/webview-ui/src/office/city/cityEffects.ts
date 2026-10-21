/**
 * THE CITY'S DECORATIVE EFFECTS STATE (living city 6–9): the viewer's presentation choices, the lighting
 * level currently SHOWN, and the weather field. One instance lives in OfficeState; the renderer reads it.
 *
 * It is local decoration and nothing else. It reads no agent, Mission, assignment, approval or delivery
 * state and writes none; real agents only appear to the renderer as positions that get a light pool.
 *
 * Advancing it is the game loop's job (OfficeState.update): the loop's hidden guard means nothing here
 * advances while the view is hidden, and `catchUp` does not exist: lighting is a function of the clock
 * (clock mode) or a still stage, and weather has no state worth recovering.
 */
import type { CityPresentation } from '../../../../core/src/cityPresentation.js';
import { DEFAULT_CITY_PRESENTATION } from '../../../../core/src/cityPresentation.js';
import { approachLevel, lightingStatus, targetLevel } from './cityLight.js';
import { WeatherField } from './cityWeather.js';

export class CityEffects {
  private prefs: CityPresentation = { ...DEFAULT_CITY_PRESENTATION };
  /** The darkness level shown now (0 day .. 1 night). */
  level = 0;
  readonly weather = new WeatherField();

  get presentation(): Readonly<CityPresentation> {
    return this.prefs;
  }

  /** Take the viewer's choices. Under reduced motion the shown level jumps to its target at once. */
  setPresentation(next: CityPresentation, nowMs: number, reducedMotion: boolean): void {
    this.prefs = { ...next };
    this.weather.setKind(next.weather);
    if (reducedMotion) this.level = targetLevel(next.lighting, nowMs, true);
  }

  /** One game-loop step. Weather moves only when motion is allowed; the level follows its target. */
  update(dt: number, nowMs: number, reducedMotion: boolean): void {
    this.level = approachLevel(
      this.level,
      targetLevel(this.prefs.lighting, nowMs, reducedMotion),
      dt,
      reducedMotion,
    );
    if (!reducedMotion) this.weather.update(dt);
  }

  /** The words for the current lighting choice (for the control's live status). */
  lightingStatus(nowMs: number): string {
    return lightingStatus(this.prefs.lighting, nowMs);
  }
}
