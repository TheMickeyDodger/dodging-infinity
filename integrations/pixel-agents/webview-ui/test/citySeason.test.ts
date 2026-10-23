/**
 * ITEM 18, MILESTONE 1: the season engine, exactly. Timing is asserted in milliseconds with injected steps; nothing here reads a
 * wall clock. The recolour art and its caches are tested in citySeasonArt.test.ts.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import * as C from '../src/constants.ts';
import type { SeasonFrame } from '../src/office/city/citySeason.ts';
import {
  minutesLeft,
  nextSeason,
  SeasonEngine,
  SEASONS,
  seasonStatus,
} from '../src/office/city/citySeason.ts';

const INTERVAL = 1_800_000;

/** Run `ms` of visible running time through the engine in 0.1 s game-loop steps; returns how many season changes happened. */
function run(engine: SeasonEngine, ms: number, reduced = false): number {
  let changes = 0;
  for (let t = 0; t < ms; t += 100) if (engine.update(0.1, reduced)) changes++;
  return changes;
}

test('the bounds are exactly these values', () => {
  assert.equal(C.CITY_SEASON_INTERVAL_MS, 1_800_000); // 30 minutes
  assert.equal(C.CITY_CLOCK_MAX_STEP_SEC, 0.25);
  assert.equal(C.CITY_SEASON_FADE_MS, 3_000);
  assert.equal(C.CITY_SEASON_CACHE_MAX_ENTRIES, 2_048);
  assert.deepEqual([...SEASONS], ['spring', 'summer', 'autumn', 'winter']);
});

test('a new engine is Automatic, in summer, with a full thirty minutes, not paused, and plain summer draws nothing extra', () => {
  const e = new SeasonEngine();
  assert.deepEqual(e.state(), { mode: 'auto', season: 'summer', paused: false, remainingMs: INTERVAL });
  assert.equal(e.frame(), null); // the world exactly as it has always looked
});

test('Automatic turns summer -> autumn -> winter -> spring -> summer, thirty minutes of running time each', () => {
  const e = new SeasonEngine();
  const seen: string[] = [e.season];
  // just short of the boundary: still summer, with 100 ms left
  run(e, INTERVAL - 100);
  assert.equal(e.season, 'summer');
  assert.equal(e.remainingMs, 100);
  // the next step crosses it
  assert.equal(e.update(0.1, false), true);
  assert.equal(e.season, 'autumn');
  assert.equal(e.remainingMs, INTERVAL); // a fresh full interval
  seen.push(e.season);
  for (let k = 0; k < 3; k++) {
    run(e, INTERVAL);
    seen.push(e.season);
  }
  assert.deepEqual(seen, ['summer', 'autumn', 'winter', 'spring', 'summer']);
  assert.equal(nextSeason('winter'), 'spring');
});

test('Manual holds the chosen season for ever, and Automatic then starts a FRESH full interval from it', () => {
  const e = new SeasonEngine();
  run(e, 600_000); // ten minutes into summer
  assert.equal(e.remainingMs, INTERVAL - 600_000);
  assert.equal(e.setManual('winter', false), true);
  assert.equal(e.mode, 'manual');
  assert.equal(e.season, 'winter');
  run(e, 3 * INTERVAL); // three intervals pass: still winter
  assert.equal(e.season, 'winter');
  assert.equal(e.remainingMs, INTERVAL);
  e.setAutomatic();
  assert.deepEqual([e.mode, e.season, e.remainingMs], ['auto', 'winter', INTERVAL]);
  run(e, INTERVAL - 100);
  assert.equal(e.season, 'winter');
  run(e, 100);
  assert.equal(e.season, 'spring');
});

test('choosing the season already shown changes nothing visible, but still switches to Manual', () => {
  const e = new SeasonEngine();
  assert.equal(e.setManual('summer', false), false);
  assert.equal(e.mode, 'manual');
  assert.equal(e.frame(), null);
});

test('Pause freezes the timer and keeps the remaining time; Resume carries on from exactly there', () => {
  const e = new SeasonEngine();
  run(e, 900_000);
  const left = e.remainingMs;
  assert.equal(left, INTERVAL - 900_000);
  e.pause();
  run(e, 10 * INTERVAL); // ten intervals while paused: nothing moves
  assert.equal(e.season, 'summer');
  assert.equal(e.remainingMs, left);
  assert.equal(e.paused, true);
  e.resume();
  run(e, left - 100);
  assert.equal(e.season, 'summer');
  run(e, 100);
  assert.equal(e.season, 'autumn');
});

test('NO CATCH-UP: a long step is clipped to the clock cap, so a hidden tab or a stalled frame replays nothing', () => {
  const e = new SeasonEngine();
  // an hour-long "step" (a tab that was hidden) takes exactly one capped step: 250 ms
  e.update(3600, false);
  assert.equal(e.remainingMs, INTERVAL - 250);
  assert.equal(e.season, 'summer');
  // a boundary overshoot is not carried: the new interval is a FRESH full one
  const near = new SeasonEngine();
  run(near, INTERVAL - 100);
  near.update(3600, false);
  assert.equal(near.season, 'autumn');
  assert.equal(near.remainingMs, INTERVAL);
  // non-positive and non-finite steps do nothing
  const frozen = new SeasonEngine();
  for (const dt of [0, -1, Number.NaN]) assert.equal(frozen.update(dt, false), false);
  assert.equal(frozen.remainingMs, INTERVAL);
});

test('a change cross-fades over three seconds; reduced motion swaps at once', () => {
  const e = new SeasonEngine();
  e.setManual('winter', false);
  let f = e.frame() as SeasonFrame;
  assert.deepEqual(f, { season: 'winter', from: 'summer', t: 0 });
  run(e, 1_500);
  f = e.frame() as SeasonFrame;
  assert.equal(f.from, 'summer');
  assert.ok(Math.abs(f.t - 0.5) < 1e-9, `half way, got ${f.t}`);
  run(e, 1_500);
  assert.deepEqual(e.frame(), { season: 'winter', from: null, t: 1 });

  const still = new SeasonEngine();
  still.setManual('autumn', true);
  assert.deepEqual(still.frame(), { season: 'autumn', from: null, t: 1 }); // no fade at all
  const auto = new SeasonEngine();
  run(auto, INTERVAL, true);
  assert.deepEqual(auto.frame(), { season: 'autumn', from: null, t: 1 });
});

test('the clock keeps running under reduced motion (it is time, not motion)', () => {
  const e = new SeasonEngine();
  assert.equal(run(e, 4 * INTERVAL, true), 4);
  assert.equal(e.season, 'summer');
});

test('the status words say mode, season and time left, and what Pause and Manual mean', () => {
  const e = new SeasonEngine();
  assert.equal(seasonStatus(e.state()), 'Automatic · Summer · 30 min left');
  run(e, 18 * 60_000 + 1_000);
  assert.equal(seasonStatus(e.state()), 'Automatic · Summer · 12 min left');
  e.pause();
  assert.equal(seasonStatus(e.state()), 'Paused on Summer, 12 min left. Resume to carry on.');
  e.resume();
  e.setManual('winter', true);
  assert.equal(seasonStatus(e.state()), 'Held on Winter until you choose Automatic.');
  assert.equal(minutesLeft(59_000), 'less than a minute left');
  assert.equal(minutesLeft(60_001), '2 min left');
});

// ── round 3: reduced motion switched ON while a cross-fade is running settles it at once ──────────────────────────────────────

test('REDUCED MOTION SWITCHED ON MID-FADE SETTLES THE SEASON CROSS-FADE AT ONCE, to exactly its target, and switching it off again restarts nothing', () => {
  const e = new SeasonEngine();
  e.setManual('winter', false); // a fade summer -> winter begins
  run(e, 500); // 0.5 s of the 3 s: t = 1/6, the state the reviewer reproduced
  const mid = e.frame() as SeasonFrame;
  assert.equal(mid.from, 'summer');
  assert.ok(Math.abs(mid.t - 1 / 6) < 1e-9, `mid-fade t = ${mid.t}`);
  // the preference turns ON: the very next step (any size, even none) shows the target, with no fade left
  e.update(0.1, true);
  assert.deepEqual(e.frame(), { season: 'winter', from: null, t: 1 });
  // the same with a zero-length step, and with a fade that has just begun
  const zero = new SeasonEngine();
  zero.setManual('autumn', false);
  assert.deepEqual(zero.frame(), { season: 'autumn', from: 'summer', t: 0 });
  zero.update(0, true);
  assert.deepEqual(zero.frame(), { season: 'autumn', from: null, t: 1 });
  // an AUTOMATIC change that is mid-fade settles too
  const auto = new SeasonEngine();
  run(auto, INTERVAL); // the boundary: summer -> autumn, fading
  run(auto, 1_000);
  assert.equal((auto.frame() as SeasonFrame).from, 'summer');
  auto.update(0.1, true);
  assert.deepEqual(auto.frame(), { season: 'autumn', from: null, t: 1 });
  // (c) switching it back OFF restarts nothing: the settled state stays, and the clock was never touched by the settling
  const before = e.state();
  run(e, 5_000); // reduced motion off again
  assert.deepEqual(e.frame(), { season: 'winter', from: null, t: 1 });
  assert.deepEqual(e.state(), before);
  const auto2 = auto.state();
  run(auto, 10_000);
  assert.equal(auto.state().remainingMs, auto2.remainingMs - 10_000, 'the season clock simply carried on');
});
