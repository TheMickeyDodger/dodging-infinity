/**
 * REGIONAL WEATHER (item 18): the weather each region of the world shows, and the schedule that changes it. DOM-free.
 *
 * - MODES: Automatic (each region follows its own seeded schedule), or one MANUAL kind (Clear, Rain, Light snow, Heavy snow) that
 *   applies to every region at once and wins over the schedule until Automatic is chosen again.
 * - THE SCHEDULE: each region slot re-draws after an interval drawn uniformly from [CITY_WEATHER_INTERVAL_MIN_MS,
 *   CITY_WEATHER_INTERVAL_MAX_MS] of VISIBLE RUNNING time, and the kind it draws comes from the ELIGIBILITY table below for the
 *   season and the region's class. A draw is a PURE function of (seed, slot, that slot's step count), so the same seed and the
 *   same history give the same weather. Clear is always eligible and weighted, so weather always returns to clear.
 * - NO CATCH-UP: time passes only through `update(dt)` (OfficeState.update, the visible game loop), each call clipped to
 *   CITY_CLOCK_MAX_STEP_SEC. Entering Automatic gives every region a FRESH kind and interval; nothing overdue is replayed.
 * - FADES: a region changing kind fades the old kind out and the new one in over CITY_WEATHER_FADE_MS (at once under reduced
 *   motion), so weather never pops.
 * - FIELDS: one bounded particle field per kind (rain, light snow, heavy snow). A field advances only while some region shows
 *   its kind and motion is allowed; the renderer draws a particle only over an OUTDOOR tile of a region showing that kind.
 *
 * It is decoration and nothing else: it reads no agent, Mission, assignment, approval or delivery state, no location, no weather
 * service and no calendar, and it implies no forecast.
 */
import type { CityPresentationV2 } from '../../../../core/src/cityPresentation.js';
import { CITY_PRESENTATION_REGION_REMAINING_MAX_MS } from '../../../../core/src/cityPresentation.js';
import {
  CITY_CLOCK_MAX_STEP_SEC,
  CITY_HEAVY_SNOW_PARTICLES,
  CITY_REGION_SLOTS,
  CITY_WEATHER_BALANCE_RATE,
  CITY_WEATHER_FADE_MS,
  CITY_WEATHER_INTERVAL_MAX_MS,
  CITY_WEATHER_INTERVAL_MIN_MS,
  CITY_WEATHER_MAX_WET_DRAWS,
  CITY_WEATHER_PARTICLE_TOTAL,
  CITY_WEATHER_SCHEDULE_SEED,
  CITY_WEATHER_STEP_LIMIT,
} from '../../constants.js';
import { SeededRng } from '../ambient/ambientRng.js';
import { REGION_CITY } from './cityRegions.js';
import type { Season } from './citySeason.js';
import { WeatherField } from './cityWeather.js';

export const WEATHER_KINDS = ['clear', 'rain', 'light-snow', 'heavy-snow'] as const;
export type WeatherKind = (typeof WEATHER_KINDS)[number];
export type WeatherMode = 'auto' | WeatherKind;
export const WEATHER_MODES: readonly WeatherMode[] = ['auto', ...WEATHER_KINDS];

/** Plain words for a kind. */
export const WEATHER_KIND_NAMES: Readonly<Record<WeatherKind, string>> = {
  clear: 'clear',
  rain: 'rain',
  'light-snow': 'light snow',
  'heavy-snow': 'heavy snow',
};

type Weighted = ReadonlyArray<readonly [WeatherKind, number]>;

/** Leading outputs of each seeded generator that a draw discards: seeds that differ in a few bits give correlated first outputs. */
export const WEATHER_DRAW_WARMUP = 3;

/**
 * AUTOMATIC ELIGIBILITY and WEIGHTS: which kinds a region of each class may draw in each season, and how likely each is (a draw
 * picks a kind with probability weight / total). Automatic heavy snow occurs ONLY in winter and ONLY in Woodland and Highlands;
 * a manual Heavy snow is available in any season. A layout without districts uses the City row.
 */
const CLEAR_RAIN_SPRING: Weighted = [
  ['clear', 5],
  ['rain', 3],
];
const CLEAR_RAIN_SUMMER: Weighted = [
  ['clear', 5],
  ['rain', 2],
];
const CLEAR_RAIN_AUTUMN: Weighted = [
  ['clear', 4],
  ['rain', 3],
];
const HIGHLANDS_SPRING: Weighted = [
  ['clear', 4],
  ['rain', 3],
  ['light-snow', 2],
];
const HIGHLANDS_AUTUMN: Weighted = [
  ['clear', 4],
  ['rain', 3],
  ['light-snow', 2],
];
const CITY_WINTER: Weighted = [
  ['clear', 5],
  ['light-snow', 3],
];
const COAST_WINTER: Weighted = [
  ['clear', 5],
  ['light-snow', 2],
  ['rain', 2],
];
const WOODLAND_WINTER: Weighted = [
  ['clear', 4],
  ['light-snow', 3],
  ['heavy-snow', 2],
];
const HIGHLANDS_WINTER: Weighted = [
  ['clear', 3],
  ['light-snow', 3],
  ['heavy-snow', 3],
];

export const WEATHER_ELIGIBILITY: Readonly<Record<Season, readonly Weighted[]>> = {
  // indexed by region class: City, Coast, Woodland, Highlands, Farmland
  spring: [
    CLEAR_RAIN_SPRING,
    CLEAR_RAIN_SPRING,
    CLEAR_RAIN_SPRING,
    HIGHLANDS_SPRING,
    CLEAR_RAIN_SPRING,
  ],
  summer: [
    CLEAR_RAIN_SUMMER,
    CLEAR_RAIN_SUMMER,
    CLEAR_RAIN_SUMMER,
    CLEAR_RAIN_SUMMER,
    CLEAR_RAIN_SUMMER,
  ],
  autumn: [
    CLEAR_RAIN_AUTUMN,
    CLEAR_RAIN_AUTUMN,
    CLEAR_RAIN_AUTUMN,
    HIGHLANDS_AUTUMN,
    CLEAR_RAIN_AUTUMN,
  ],
  winter: [CITY_WINTER, COAST_WINTER, WOODLAND_WINTER, HIGHLANDS_WINTER, CITY_WINTER],
};

/** The weighted kinds a region of class `cls` may draw in `season` (a class out of range reads as City). */
export function eligibleKinds(season: Season, cls: number): Weighted {
  const row = WEATHER_ELIGIBILITY[season];
  return row[cls] ?? row[REGION_CITY];
}

/** The interval, in ms, a uniform draw `u` in [0, 1) gives: the closed range [MIN, MAX], both ends reachable. */
export function scheduleIntervalMs(u: number): number {
  const span = CITY_WEATHER_INTERVAL_MAX_MS - CITY_WEATHER_INTERVAL_MIN_MS + 1;
  return CITY_WEATHER_INTERVAL_MIN_MS + Math.min(span - 1, Math.floor(u * span));
}

/** The kind a uniform draw `u` in [0, 1) picks from a weighted list. */
export function pickKind(table: Weighted, u: number): WeatherKind {
  let total = 0;
  for (const [, w] of table) total += w;
  let roll = u * total;
  for (const [kind, w] of table) {
    if (roll < w) return kind;
    roll -= w;
  }
  return table[table.length - 1][0];
}

/**
 * One scheduled draw: a pure function of the seed, the region slot, that slot's step count, the season, the region's class and
 * `wetStreak`, the number of consecutive non-clear draws the slot has just made. The draw consumes the same random numbers whatever
 * the streak (so the interval never depends on it); at CITY_WEATHER_MAX_WET_DRAWS the KIND is forced to clear, which is what
 * guarantees weather always returns to clear and never lasts longer than that many intervals.
 */
export function drawWeather(
  seed: number,
  slot: number,
  step: number,
  season: Season,
  cls: number,
  wetStreak = 0,
): { kind: WeatherKind; intervalMs: number } {
  const mixed = (seed ^ Math.imul(slot + 1, 0x9e3779b1) ^ Math.imul(step + 1, 0x85ebca6b)) >>> 0;
  const rng = new SeededRng(mixed);
  for (let i = 0; i < WEATHER_DRAW_WARMUP; i++) rng.next();
  const intervalMs = scheduleIntervalMs(rng.next());
  const rolled = pickKind(eligibleKinds(season, cls), rng.next());
  const kind = wetStreak >= CITY_WEATHER_MAX_WET_DRAWS ? 'clear' : rolled;
  return { kind, intervalMs };
}

/** The first `count` draws of one region slot, folding the wet streak exactly as the live schedule does (a test and report helper). */
export function scheduleSequence(
  seed: number,
  slot: number,
  season: Season,
  cls: number,
  count: number,
): Array<{ kind: WeatherKind; intervalMs: number }> {
  const out: Array<{ kind: WeatherKind; intervalMs: number }> = [];
  let streak = 0;
  for (let step = 0; step < count; step++) {
    const d = drawWeather(seed, slot, step, season, cls, streak);
    streak = d.kind === 'clear' ? 0 : streak + 1;
    out.push(d);
  }
  return out;
}

/** One region as the controls describe it: its plain name and the kind it shows now. */
export interface RegionKindEntry {
  slot: number;
  name: string;
  kind: WeatherKind;
}

/** The words for the weather control's status line (plain English; never a forecast). */
export function weatherStatus(mode: WeatherMode, regions: readonly RegionKindEntry[]): string {
  if (mode !== 'auto') {
    const what =
      mode === 'clear'
        ? 'Clear skies'
        : `${WEATHER_KIND_NAMES[mode][0].toUpperCase()}${WEATHER_KIND_NAMES[mode].slice(1)}`;
    return `${what} everywhere. Held until you choose Automatic.`;
  }
  const wet = regions.filter((r) => r.kind !== 'clear');
  if (wet.length === 0) return 'Automatic regional weather: clear skies everywhere right now.';
  const parts = wet.map((r) =>
    `${WEATHER_KIND_NAMES[r.kind]} in the ${r.name}`.replace('in the Everywhere', 'everywhere'),
  );
  const all = wet.length === regions.length;
  return `Automatic regional weather: ${parts.join(', ')}.${all ? '' : ' Elsewhere clear.'}`;
}

interface SlotState {
  kind: WeatherKind;
  /** The kind fading out while `fadeMs` runs (equal to `kind` when no fade is in progress). */
  prev: WeatherKind;
  fadeMs: number;
  /** Visible running time until this slot's next automatic draw. */
  remainingMs: number;
  /** How many automatic draws this slot has made (feeds the next draw). */
  step: number;
  /** Consecutive non-clear automatic draws just made (feeds the next draw). */
  wetStreak: number;
}

/** A plain snapshot of one region slot, for the controls and for tests. */
export interface RegionWeatherState {
  slot: number;
  kind: WeatherKind;
  remainingMs: number;
  step: number;
  /** Consecutive non-clear automatic draws just made (the next is forced clear at CITY_WEATHER_MAX_WET_DRAWS). */
  wetStreak: number;
}

/** The saved shape of the weather (contract v2): plain values, no references into the schedule. */
export type WeatherRecord = CityPresentationV2['weather'];

export class RegionalWeather {
  private modeValue: WeatherMode = 'auto';
  private seasonValue: Season = 'summer';
  private seedValue: number;
  /** Counts every change a viewer's reload would have to know about (a mode, a season-driven redraw, an automatic draw). Never per frame. */
  private revisionValue = 0;
  private readonly slots: SlotState[] = [];
  /** Class of each slot (what the eligibility table is read with). All City until a region map says otherwise. */
  private classes: Uint8Array = new Uint8Array(CITY_REGION_SLOTS).fill(REGION_CITY);
  /** One bounded field per kind; a field is 'off' (no live particles) unless a region shows its kind. The three share ONE particle pool. */
  readonly rain = new WeatherField();
  readonly lightSnow = new WeatherField();
  readonly heavySnow = new WeatherField(CITY_HEAVY_SNOW_PARTICLES);

  /** True until anything has happened to the schedule (a step, a mode or a season change): the starting weather may still be re-drawn. */
  private pristine = true;
  /**
   * False while the classes held are only the PLACEHOLDER of a layout that has not arrived yet (`setClasses(classes, false)`: every slot reads
   * City). True otherwise: a schedule given its classes (a constructor argument, the all-City default of a bare schedule, or the real
   * layout's) treats them as real. While false no class-dependent draw is made and a restored or season-changed state waits to be verified.
   */
  private classesKnown = true;
  /** A restored or season-changed automatic state still to be checked against the real classes (every slot, once, when they are known). */
  private verifyPending = false;

  constructor(
    seed: number = CITY_WEATHER_SCHEDULE_SEED,
    season: Season = 'summer',
    classes?: Uint8Array,
  ) {
    this.seedValue = seed;
    this.seasonValue = season;
    if (classes) this.classes = Uint8Array.from(classes);
    for (let slot = 0; slot < CITY_REGION_SLOTS; slot++) {
      this.slots.push({
        kind: 'clear',
        prev: 'clear',
        fadeMs: CITY_WEATHER_FADE_MS,
        remainingMs: 0,
        step: 0,
        wetStreak: 0,
      });
    }
    this.enterAutomatic(true);
    this.syncFields(true);
  }

  get mode(): WeatherMode {
    return this.modeValue;
  }

  get season(): Season {
    return this.seasonValue;
  }

  /** The seed of the schedule. */
  get scheduleSeed(): number {
    return this.seedValue;
  }

  get revision(): number {
    return this.revisionValue;
  }

  /** The saved shape: the mode, the seed and exactly one small state per region slot (whole milliseconds inside the contract's bound). */
  persist(): WeatherRecord {
    return {
      mode: this.modeValue,
      seed: this.seedValue,
      regions: this.slots.map((s) => ({
        kind: s.kind,
        step: s.step,
        wet: s.wetStreak,
        remaining: Math.max(
          0,
          Math.min(CITY_PRESENTATION_REGION_REMAINING_MAX_MS, Math.round(s.remainingMs)),
        ),
      })),
    };
  }

  /**
   * Take saved weather (already checked against the contract) for a city in `season`: every region continues from its saved kind, draw
   * index, wet streak and time left, with no fade and NO catch-up for the time the view was closed. A saved kind that is no longer
   * eligible for the region's class (the layout changed) is replaced by the next `setClasses`, like any other class change.
   */
  restore(saved: WeatherRecord, season: Season): void {
    this.modeValue = saved.mode;
    this.seedValue = saved.seed;
    this.seasonValue = season;
    saved.regions.forEach((r, k) => {
      const s = this.slots[k];
      s.kind = r.kind;
      s.prev = r.kind;
      s.fadeMs = CITY_WEATHER_FADE_MS;
      s.remainingMs = r.remaining;
      s.step = r.step;
      s.wetStreak = r.wet;
    });
    // a manual mode shows ITS kind in every region, whatever the schedule state beside it says (a migrated v1 record carries a manual
    // mode and quiet default regions)
    if (saved.mode !== 'auto') {
      for (const s of this.slots) {
        s.kind = saved.mode;
        s.prev = saved.mode;
      }
    }
    this.pristine = false;
    this.revisionValue++;
    // A restored AUTOMATIC state is verified against the REAL classes: now if they are already known, else at the first real class
    // assignment (never against the placeholder, which would wrongly re-draw valid Highlands or Woodland weather).
    this.verifyPending = saved.mode === 'auto';
    if (this.verifyPending && this.classesKnown) this.repairIneligible();
    this.syncFields(true);
  }

  /**
   * Every automatic region must show a kind that is ELIGIBLE for its class and the season (D11). A state the product itself produces
   * always is; only a hand-edited, corrupt or foreign record can break it. Re-draw each such region deterministically (the pure draw of
   * its saved seed, draw index and wet streak) and at once, so a record can never show weather the rules forbid.
   */
  private repairIneligible(): void {
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      const eligible = eligibleKinds(this.seasonValue, this.classes[k]).some(
        ([kind]) => kind === this.slots[k].kind,
      );
      if (!eligible) this.drawSlot(k, true);
    }
    this.verifyPending = false;
  }

  slotState(slot: number): RegionWeatherState {
    const s = this.slots[slot];
    return { slot, kind: s.kind, remainingMs: s.remainingMs, step: s.step, wetStreak: s.wetStreak };
  }

  /** The kind a slot is showing (the one it is fading IN to, during a fade). */
  kindAt(slot: number): WeatherKind {
    return this.slots[slot]?.kind ?? 'clear';
  }

  /** How strongly `kind` is shown in `slot` right now, 0..1 (fades in, fades out; 0 for a slot out of range). */
  alpha(kind: WeatherKind, slot: number): number {
    const s = this.slots[slot];
    if (!s) return 0;
    const progress = Math.min(1, s.fadeMs / CITY_WEATHER_FADE_MS);
    if (s.prev === s.kind || progress >= 1) return s.kind === kind ? 1 : 0;
    if (s.kind === kind) return progress;
    if (s.prev === kind) return 1 - progress;
    return 0;
  }

  /** Whether any region shows anything but clear sky right now (a fade counts). */
  anyWeather(): boolean {
    for (const s of this.slots) if (s.kind !== 'clear' || s.prev !== 'clear') return true;
    return false;
  }

  /** The field a kind is drawn from (null for clear). */
  fieldFor(kind: WeatherKind): WeatherField | null {
    return kind === 'rain'
      ? this.rain
      : kind === 'light-snow'
        ? this.lightSnow
        : kind === 'heavy-snow'
          ? this.heavySnow
          : null;
  }

  /**
   * Tell the schedule the class of each region slot (from the layout's region map). `authoritative` is false while the map is only the
   * placeholder of the default layout (before the real layout arrives): the classes are adopted but nothing is acted on, because every slot
   * then reads City and would wrongly re-draw valid Highlands or Woodland weather; and NO automatic draw is made meanwhile (a draw reads
   * the class), so a slot whose time is up waits at zero. Once the classes are real:
   *  - a schedule that is still pristine (nothing has stepped, changed mode, changed season or been restored) draws its starting weather
   *    again from step 0 with the right classes, at once;
   *  - a restored or season-changed automatic state is verified ONCE against them, every slot (see `repairIneligible`);
   *  - later (a layout that changes), a slot whose class changed keeps its weather when that kind is still eligible for the new class and
   *    gets a fresh draw only when it is not: a layout change never resets weather that is still valid.
   */
  setClasses(classes: Uint8Array, authoritative = true): void {
    let changed = false;
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      if (this.classes[k] !== classes[k]) changed = true;
    }
    const before = this.classes;
    if (changed) this.classes = Uint8Array.from(classes);
    if (!authoritative) {
      this.classesKnown = false;
      return;
    }
    this.classesKnown = true;
    if (this.modeValue !== 'auto') {
      this.verifyPending = false;
      if (changed) this.syncFields();
      return;
    }
    if (this.pristine) {
      if (!changed) return;
      for (let k = 0; k < CITY_REGION_SLOTS; k++) {
        this.slots[k].step = 0;
        this.slots[k].wetStreak = 0;
        this.drawSlot(k, true);
      }
      this.verifyPending = false;
      this.syncFields();
      return;
    }
    if (this.verifyPending) {
      this.repairIneligible();
      this.syncFields();
      return;
    }
    if (!changed) return;
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      if (before[k] === this.classes[k]) continue;
      const eligible = eligibleKinds(this.seasonValue, this.classes[k]).some(
        ([kind]) => kind === this.slots[k].kind,
      );
      if (!eligible) this.drawSlot(k, false);
    }
    this.syncFields();
  }

  /** The season changed: an automatic region whose kind is no longer eligible gets a fresh eligible draw. Manual weather is untouched. */
  setSeason(season: Season, reducedMotion: boolean): void {
    if (season === this.seasonValue) return;
    this.pristine = false;
    this.revisionValue++;
    this.seasonValue = season;
    if (this.modeValue !== 'auto') return;
    if (!this.classesKnown) {
      // the real classes are not known yet (the placeholder reads City everywhere): check every region against them when they arrive
      this.verifyPending = true;
      return;
    }
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      const eligible = eligibleKinds(season, this.classes[k]).some(
        ([kind]) => kind === this.slots[k].kind,
      );
      if (!eligible) this.drawSlot(k, reducedMotion);
    }
    this.syncFields(reducedMotion);
  }

  /** Choose Automatic, or one manual kind for every region. */
  setMode(mode: WeatherMode, reducedMotion: boolean): void {
    this.pristine = false;
    this.revisionValue++;
    this.modeValue = mode;
    if (mode === 'auto') this.enterAutomatic(reducedMotion);
    else for (const s of this.slots) this.change(s, mode, reducedMotion);
    this.syncFields(reducedMotion);
  }

  /** Give every region a FRESH seeded kind and interval (no overdue replay). */
  private enterAutomatic(instant: boolean): void {
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      this.slots[k].wetStreak = 0; // a fresh start: whatever came before (manual weather included) is not counted
      this.drawSlot(k, instant);
    }
  }

  private change(s: SlotState, kind: WeatherKind, instant: boolean): void {
    if (s.kind === kind) return;
    s.prev = s.kind;
    s.kind = kind;
    s.fadeMs = instant ? CITY_WEATHER_FADE_MS : 0;
    if (instant) s.prev = kind;
  }

  private drawSlot(slot: number, instant: boolean): void {
    const s = this.slots[slot];
    const drawn = drawWeather(
      this.seedValue,
      slot,
      s.step,
      this.seasonValue,
      this.classes[slot],
      s.wetStreak,
    );
    s.step = (s.step + 1) % CITY_WEATHER_STEP_LIMIT; // wraps (about 3.8 years of visible running time at the shortest interval)
    this.revisionValue++;
    s.remainingMs = drawn.intervalMs;
    s.wetStreak = drawn.kind === 'clear' ? 0 : s.wetStreak + 1;
    this.change(s, drawn.kind, instant);
  }

  /**
   * One game-loop step of `dtSec` seconds: the schedule, the fades, and the fields. Each step is clipped to
   * CITY_CLOCK_MAX_STEP_SEC. Under reduced motion the particle fields do not move (one still frame), but the schedule and the
   * kinds still progress, switching at once.
   */
  update(dtSec: number, reducedMotion: boolean): void {
    // reduced motion shows every change at once, so a fade already running when the preference turns ON is settled to its target now
    // (the same state a change made under reduced motion has, particle shares included), whatever the size of this step
    if (reducedMotion) this.settleFades();
    if (!(dtSec > 0)) return;
    this.pristine = false;
    const dt = Math.min(dtSec, CITY_CLOCK_MAX_STEP_SEC);
    const dtMs = dt * 1000;
    for (let k = 0; k < CITY_REGION_SLOTS; k++) {
      const s = this.slots[k];
      if (s.prev !== s.kind) {
        s.fadeMs += dtMs;
        if (s.fadeMs >= CITY_WEATHER_FADE_MS) s.prev = s.kind;
      }
      if (this.modeValue !== 'auto') continue;
      s.remainingMs -= dtMs;
      if (s.remainingMs <= 0) {
        // A draw reads the region's CLASS. While the classes are only the placeholder (the real layout has not arrived) the slot waits at
        // zero and draws on the first step after they are real: a restored slot with no time left, or less than one step, must never
        // draw against City.
        if (this.classesKnown) this.drawSlot(k, reducedMotion);
        else s.remainingMs = 0;
      }
    }
    // a field has live particles only while some region shows its kind; the pool shares ease toward what the regions show
    this.syncFields();
    this.easeShares(dt, reducedMotion);
    if (!reducedMotion) {
      this.rain.update(dt);
      this.lightSnow.update(dt);
      this.heavySnow.update(dt);
    }
  }

  /** Settle every weather fade in progress at once: each region shows its own kind in full, and the pool shares move to what the regions show. */
  private settleFades(): void {
    let settled = false;
    for (const s of this.slots) {
      if (s.prev === s.kind) continue;
      s.prev = s.kind;
      s.fadeMs = CITY_WEATHER_FADE_MS;
      settled = true;
    }
    if (settled) this.syncFields(true);
  }

  /**
   * Make the fields match what the regions show, without advancing anything (after a mode change, or at construction). A field whose kind
   * is not shown holds no share. `instant` also moves every share straight to what it is entitled to (a restore, a reduced-motion change);
   * otherwise the shares ease in `update`.
   */
  syncFields(instant = false): void {
    this.rain.setKind(this.shown('rain') ? 'rain' : 'off');
    this.lightSnow.setKind(this.shown('light-snow') ? 'snow' : 'off');
    this.heavySnow.setKind(this.shown('heavy-snow') ? 'heavy-snow' : 'off');
    this.easeShares(0, instant);
  }

  /** Live particles across ALL fields, which is never more than CITY_WEATHER_PARTICLE_TOTAL. */
  particleTotal(): number {
    return this.rain.count + this.lightSnow.count + this.heavySnow.count;
  }

  /**
   * What each field is entitled to: the pool shared in proportion to the regions showing each kind (a fade counts for both kinds), each
   * within its own capacity. A kind shown alone gets its whole capacity (rain 520, light snow 300, heavy snow 520), exactly as before.
   *
   * THE REMAINDER RULE. Each share is `floor(520 * regions showing the kind / regions showing any kind)`, then limited to the kind's
   * capacity, and NOTHING IS REDISTRIBUTED: what flooring leaves over, and what a capacity limit frees, stays unused. So the total is never
   * above 520, and when no capacity binds it is at least 520 - (n - 1) for n kinds shown (each floor loses less than one particle):
   * 520 for one kind, 520 for two equal kinds (260 + 260), 519 for three equal kinds (173 x 3). A kind never gets more than its entitlement.
   */
  private entitlements(): [number, number, number] {
    const fields = [this.rain, this.lightSnow, this.heavySnow] as const;
    const kinds: readonly WeatherKind[] = ['rain', 'light-snow', 'heavy-snow'];
    const weights = kinds.map((kind) =>
      this.slots.reduce((n, s) => n + (s.kind === kind || s.prev === kind ? 1 : 0), 0),
    );
    const sum = weights.reduce((a, b) => a + b, 0);
    const of = (i: number): number =>
      sum === 0
        ? 0
        : Math.min(
            fields[i].capacity,
            Math.floor((CITY_WEATHER_PARTICLE_TOTAL * weights[i]) / sum),
          );
    return [of(0), of(1), of(2)];
  }

  /**
   * Move each field's pool share toward its entitlement by at most CITY_WEATHER_BALANCE_RATE a second (all the way when `instant`).
   * A field that is not shown holds nothing; shares that must fall fall first; a share only grows into the room the others have left.
   * So the live total is within CITY_WEATHER_PARTICLE_TOTAL at every moment, a transition between kinds included.
   */
  private easeShares(dtSec: number, instant: boolean): void {
    const fields = [this.rain, this.lightSnow, this.heavySnow] as const;
    const target = this.entitlements();
    const step = instant ? Infinity : CITY_WEATHER_BALANCE_RATE * Math.max(0, dtSec);
    fields.forEach((f, i) => {
      if (f.kind === 'off') f.setShare(0);
      else if (f.share > target[i]) f.setShare(Math.max(target[i], f.share - step));
    });
    let room = CITY_WEATHER_PARTICLE_TOTAL;
    for (const f of fields) if (f.kind !== 'off') room -= f.share;
    fields.forEach((f, i) => {
      if (f.kind === 'off' || f.share >= target[i]) return;
      const grow = Math.min(step, target[i] - f.share, Math.max(0, room));
      if (grow > 0) {
        f.setShare(f.share + grow);
        room -= grow;
      }
    });
  }

  private shown(kind: WeatherKind): boolean {
    for (const s of this.slots) if (s.kind === kind || s.prev === kind) return true;
    return false;
  }
}
