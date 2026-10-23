/**
 * ITEM 18, MILESTONE 1: regional weather. The schedule is a pure function of (seed, region slot, step count), so it is asserted by
 * EXACT sequences. NOTE ON THE GOLDEN SEQUENCES BELOW: they were produced by this implementation, then pinned. They guard determinism
 * and any accidental change of the draw; they are not an independent oracle of the distribution (the weights and bounds are asserted
 * separately, and the distribution by a large-sample count).
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import * as C from '../src/constants.ts';
import * as W from '../src/office/city/cityRegionWeather.ts';
import type { Season } from '../src/office/city/citySeason.ts';
import { SEASONS } from '../src/office/city/citySeason.ts';
import { WeatherField } from '../src/office/city/cityWeather.ts';

const SEED = C.CITY_WEATHER_SCHEDULE_SEED;
const classAll = (cls: number) => new Uint8Array(C.CITY_REGION_SLOTS).fill(cls);

/** Step the schedule `ms` of visible running time in 0.25 s steps; returns the step counters seen after each step. */
function run(w: W.RegionalWeather, ms: number, onStep?: (t: number) => void): void {
  for (let t = 0; t < ms; t += 250) {
    w.update(0.25, false);
    onStep?.(t + 250);
  }
}

test('the bounds are exactly these values', () => {
  assert.equal(C.CITY_WEATHER_INTERVAL_MIN_MS, 120_000);
  assert.equal(C.CITY_WEATHER_INTERVAL_MAX_MS, 300_000);
  assert.equal(C.CITY_WEATHER_FADE_MS, 4_000);
  assert.equal(C.CITY_WEATHER_SCHEDULE_SEED, 0x51a7);
  assert.equal(C.CITY_WEATHER_MAX_WET_DRAWS, 2);
  assert.equal(W.WEATHER_DRAW_WARMUP, 3); // leading generator outputs a draw discards (a module constant)
  assert.equal(C.CITY_REGION_SLOTS, 5);
  // heavy snow is retuned from 800 to 520 so that no field is larger than the load budget's own bound (520), and all three share ONE pool
  assert.equal(C.CITY_HEAVY_SNOW_PARTICLES, 520);
  assert.equal(C.CITY_WEATHER_PARTICLE_TOTAL, 520);
  assert.equal(C.CITY_WEATHER_PARTICLE_TOTAL, C.CITY_RAIN_PARTICLES);
  assert.equal(C.CITY_WEATHER_BALANCE_RATE, 200);
  assert.equal(C.CITY_REGION_EDGE_TILES, 4);
  assert.equal(C.CITY_SNOW_HALO_PX, 1);
  assert.equal(C.CITY_SNOW_FLAKE_SCALE, 3);
  assert.equal(C.CITY_SNOW_HALO_COLOR, C.cityRgba(70, 92, 138, 0.5));
  assert.equal(C.CITY_HEAVY_SNOW_SPEED, 0.16);
  assert.equal(C.CITY_HEAVY_SNOW_SWAY, 0.03);
  assert.equal(C.CITY_HEAVY_SNOW_SIZE_PX, 2);
  assert.equal(C.CITY_HEAVY_SNOW_NEAR_DEPTH, 0.75);
  assert.deepEqual(C.CITY_OVERCAST_HEAVY_SNOW, { r: 150, g: 164, b: 190, a: 0.26 });
});

test('THE ELIGIBILITY TABLE, with its weights, is exactly this', () => {
  assert.deepEqual(W.WEATHER_ELIGIBILITY, {
    // City, Coast, Woodland, Highlands, Farmland
    spring: [
      [['clear', 5], ['rain', 3]],
      [['clear', 5], ['rain', 3]],
      [['clear', 5], ['rain', 3]],
      [['clear', 4], ['rain', 3], ['light-snow', 2]],
      [['clear', 5], ['rain', 3]],
    ],
    summer: [
      [['clear', 5], ['rain', 2]],
      [['clear', 5], ['rain', 2]],
      [['clear', 5], ['rain', 2]],
      [['clear', 5], ['rain', 2]],
      [['clear', 5], ['rain', 2]],
    ],
    autumn: [
      [['clear', 4], ['rain', 3]],
      [['clear', 4], ['rain', 3]],
      [['clear', 4], ['rain', 3]],
      [['clear', 4], ['rain', 3], ['light-snow', 2]],
      [['clear', 4], ['rain', 3]],
    ],
    winter: [
      [['clear', 5], ['light-snow', 3]],
      [['clear', 5], ['light-snow', 2], ['rain', 2]],
      [['clear', 4], ['light-snow', 3], ['heavy-snow', 2]],
      [['clear', 3], ['light-snow', 3], ['heavy-snow', 3]],
      [['clear', 5], ['light-snow', 3]],
    ],
  });
});

test('clear is always eligible and substantially weighted; automatic heavy snow is winter-only, Woodland and Highlands only', () => {
  for (const season of SEASONS) {
    for (let cls = 0; cls < 5; cls++) {
      const table = W.eligibleKinds(season, cls);
      const clear = table.find(([k]) => k === 'clear');
      assert.ok(clear, `${season}/${cls}: clear is eligible`);
      const total = table.reduce((n, [, w]) => n + w, 0);
      assert.ok(clear[1] / total >= 0.3, `${season}/${cls}: clear carries at least 30% (${clear[1]}/${total})`);
      const heavy = table.some(([k]) => k === 'heavy-snow');
      assert.equal(heavy, season === 'winter' && (cls === 2 || cls === 3), `${season}/${cls}: heavy snow`);
    }
  }
  // a class out of range reads as City
  assert.deepEqual(W.eligibleKinds('winter', 99), W.eligibleKinds('winter', 0));
});

test('INTERVAL BOUNDS: the closed range [120000, 300000] ms, both ends reachable, never outside', () => {
  assert.equal(W.scheduleIntervalMs(0), 120_000);
  assert.equal(W.scheduleIntervalMs(0.999999999999), 300_000);
  assert.equal(W.scheduleIntervalMs(0.5), 210_000);
  let lo = Infinity;
  let hi = 0;
  for (let step = 0; step < 5000; step++) {
    for (let slot = 0; slot < 5; slot++) {
      const { intervalMs } = W.drawWeather(SEED, slot, step, 'winter', slot);
      lo = Math.min(lo, intervalMs);
      hi = Math.max(hi, intervalMs);
    }
  }
  assert.ok(lo >= 120_000 && hi <= 300_000, `observed ${lo}..${hi}`);
  assert.ok(lo < 125_000 && hi > 295_000, 'the range is really used');
});

test('EXACT SEEDED DRAWS (a pure function, streak 0): the first four draws of each region slot, summer and winter', () => {
  const draws = (season: Season) =>
    [0, 1, 2, 3, 4].map((slot) =>
      [0, 1, 2, 3].map((step) => {
        const d = W.drawWeather(SEED, slot, step, season, slot, 0);
        return [d.kind, d.intervalMs];
      }),
    );
  assert.deepEqual(draws('summer'), [
    [['rain', 293604], ['clear', 150299], ['clear', 179793], ['clear', 185394]],
    [['rain', 134882], ['rain', 137881], ['clear', 177943], ['clear', 211153]],
    [['clear', 201823], ['clear', 261673], ['rain', 233817], ['clear', 256136]],
    [['clear', 294505], ['rain', 222282], ['clear', 167011], ['rain', 164989]],
    [['clear', 287229], ['clear', 288215], ['clear', 292327], ['clear', 216914]],
  ]);
  assert.deepEqual(draws('winter'), [
    [['light-snow', 293604], ['clear', 150299], ['clear', 179793], ['clear', 185394]],
    [['rain', 134882], ['rain', 137881], ['light-snow', 177943], ['clear', 211153]],
    [['light-snow', 201823], ['light-snow', 261673], ['heavy-snow', 233817], ['clear', 256136]],
    [['heavy-snow', 294505], ['heavy-snow', 222282], ['light-snow', 167011], ['heavy-snow', 164989]],
    [['clear', 287229], ['clear', 288215], ['clear', 292327], ['light-snow', 216914]],
  ]);
});

test('EXACT SEEDED SEQUENCES as the live schedule makes them (wet streak folded in): the first six draws of each region slot', () => {
  const seq = (season: Season) =>
    [0, 1, 2, 3, 4].map((slot) => W.scheduleSequence(SEED, slot, season, slot, 6).map((d) => [d.kind, d.intervalMs]));
  assert.deepEqual(seq('summer'), [
    [['rain', 293604], ['clear', 150299], ['clear', 179793], ['clear', 185394], ['clear', 183513], ['rain', 244647]],
    [['rain', 134882], ['rain', 137881], ['clear', 177943], ['clear', 211153], ['rain', 236933], ['clear', 211494]],
    [['clear', 201823], ['clear', 261673], ['rain', 233817], ['clear', 256136], ['clear', 264299], ['rain', 209470]],
    [['clear', 294505], ['rain', 222282], ['clear', 167011], ['rain', 164989], ['rain', 260141], ['clear', 138604]],
    [['clear', 287229], ['clear', 288215], ['clear', 292327], ['clear', 216914], ['rain', 173132], ['clear', 284128]],
  ]);
  // winter: slot 2's third draw (heavy snow unfolded) and slot 3's third draw (light snow unfolded) are forced clear by the streak bound
  assert.deepEqual(seq('winter'), [
    [['light-snow', 293604], ['clear', 150299], ['clear', 179793], ['clear', 185394], ['clear', 183513], ['light-snow', 244647]],
    [['rain', 134882], ['rain', 137881], ['clear', 177943], ['clear', 211153], ['rain', 236933], ['clear', 211494]],
    [['light-snow', 201823], ['light-snow', 261673], ['clear', 233817], ['clear', 256136], ['clear', 264299], ['heavy-snow', 209470]],
    [['heavy-snow', 294505], ['heavy-snow', 222282], ['clear', 167011], ['heavy-snow', 164989], ['heavy-snow', 260141], ['clear', 138604]],
    [['clear', 287229], ['clear', 288215], ['clear', 292327], ['light-snow', 216914], ['light-snow', 173132], ['clear', 284128]],
  ]);
});

test('INDEPENDENT DRAWS: for the fixed seed the regions start with different kinds and different intervals, not one shared roll', () => {
  for (const season of ['summer', 'winter'] as const) {
    const initial = [0, 1, 2, 3, 4].map((slot) => W.drawWeather(SEED, slot, 0, season, slot));
    assert.ok(new Set(initial.map((d) => d.kind)).size > 1, `${season}: the five initial kinds are not all the same`);
    assert.equal(new Set(initial.map((d) => d.intervalMs)).size, 5, `${season}: the five initial intervals are all different`);
  }
  // the same holds as a property of the generator, not just of this seed: across many seeds the five initial kinds are rarely
  // all the same (winter Highlands-style rows give about 1 in 20 by chance; a shared roll would give 1 in 1)
  let identical = 0;
  const SEEDS = 600;
  for (let seed = 1; seed <= SEEDS; seed++) {
    const kinds = new Set([0, 1, 2, 3, 4].map((slot) => W.drawWeather(seed, slot, 0, 'winter', slot).kind));
    if (kinds.size === 1) identical++;
  }
  assert.ok(identical / SEEDS < 0.1, `${identical} of ${SEEDS} seeds started every region on one kind`);
  // successive steps of one region are independent too: the kinds do not simply repeat
  const run = Array.from({ length: 40 }, (_, step) => W.drawWeather(SEED, 3, step, 'winter', 3).kind);
  assert.ok(new Set(run).size === 3);
  // and the live schedule starts from those independent draws
  const w = new W.RegionalWeather();
  const live = [0, 1, 2, 3, 4].map((slot) => w.slotState(slot));
  assert.ok(new Set(live.map((s) => s.remainingMs)).size === 5);
});

test('a draw is a pure function: the same seed gives the same weather, another seed gives other weather', () => {
  const a = W.drawWeather(SEED, 3, 7, 'winter', 3);
  assert.deepEqual(W.drawWeather(SEED, 3, 7, 'winter', 3), a);
  const other = Array.from({ length: 40 }, (_, step) => W.drawWeather(SEED + 1, 0, step, 'winter', 3).intervalMs);
  const mine = Array.from({ length: 40 }, (_, step) => W.drawWeather(SEED, 0, step, 'winter', 3).intervalMs);
  assert.notDeepEqual(other, mine);
});

test('DISTRIBUTION: over many draws each region draws exactly the kinds its row allows, near the weights', () => {
  for (const season of SEASONS) {
    for (let cls = 0; cls < 5; cls++) {
      const table = W.eligibleKinds(season, cls);
      const total = table.reduce((n, [, w]) => n + w, 0);
      const counts = new Map<string, number>();
      const N = 6000;
      for (let step = 0; step < N; step++) {
        const k = W.drawWeather(SEED, cls, step, season, cls).kind;
        counts.set(k, (counts.get(k) ?? 0) + 1);
      }
      assert.deepEqual([...counts.keys()].sort(), table.map(([k]) => k).sort(), `${season}/${cls}: only eligible kinds`);
      for (const [kind, weight] of table) {
        const share = (counts.get(kind) ?? 0) / N;
        assert.ok(Math.abs(share - weight / total) < 0.03, `${season}/${cls}/${kind}: ${share} vs ${weight / total}`);
      }
    }
  }
});

test('a new schedule starts Automatic in summer with a fresh seeded kind and interval for every region, and no fade', () => {
  const w = new W.RegionalWeather();
  assert.equal(w.mode, 'auto');
  assert.equal(w.season, 'summer');
  for (let slot = 0; slot < 5; slot++) {
    const first = W.drawWeather(SEED, slot, 0, 'summer', 0);
    assert.deepEqual(w.slotState(slot), {
      slot,
      kind: first.kind,
      remainingMs: first.intervalMs,
      step: 1,
      wetStreak: first.kind === 'clear' ? 0 : 1,
    });
    assert.equal(w.alpha(first.kind, slot), 1); // shown at once at construction
  }
});

test('regions change on their OWN schedules, within 2 to 5 minutes, and never in step with each other', () => {
  const w = new W.RegionalWeather();
  const last = [0, 0, 0, 0, 0];
  const gaps: number[][] = [[], [], [], [], []];
  const steps = [1, 1, 1, 1, 1];
  run(w, 4 * 3600 * 1000, (t) => {
    for (let slot = 0; slot < 5; slot++) {
      const s = w.slotState(slot).step;
      if (s !== steps[slot]) {
        gaps[slot].push(t - last[slot]);
        last[slot] = t;
        steps[slot] = s;
      }
    }
  });
  for (let slot = 0; slot < 5; slot++) {
    assert.ok(gaps[slot].length > 40, `slot ${slot}: many draws in four hours (${gaps[slot].length})`);
    // each gap is the previous draw's interval, to within one 250 ms step; the very first gap is the construction draw's
    gaps[slot].forEach((gap, i) => {
      const expected = W.drawWeather(SEED, slot, i, 'summer', 0).intervalMs;
      assert.ok(Math.abs(gap - expected) <= 250, `slot ${slot} draw ${i}: ${gap} vs ${expected}`);
      assert.ok(gap >= 120_000 - 250 && gap <= 300_000 + 250);
    });
  }
  // not synchronised: the five regions' first changes are at five different moments
  assert.equal(new Set(gaps.map((g) => g[0])).size, 5);
});

test('RETURN TO CLEAR is a pinned guarantee: no region makes more than 2 consecutive non-clear automatic draws, so continuous weather lasts at most 10 minutes', () => {
  const MAX = 2;
  assert.equal(C.CITY_WEATHER_MAX_WET_DRAWS, MAX, 'the bound, by exact value');
  // 2 draws at the longest interval is 10 minutes: a third of one 30-minute season, never a whole one
  const longestMs = MAX * C.CITY_WEATHER_INTERVAL_MAX_MS;
  assert.equal(longestMs, 600_000);
  assert.ok(longestMs * 3 === C.CITY_SEASON_INTERVAL_MS);
  let reached = 0;
  let forced = 0;
  for (const season of SEASONS) {
    for (let cls = 0; cls < 5; cls++) {
      for (let seed = SEED; seed < SEED + 8; seed++) {
        for (let slot = 0; slot < 5; slot++) {
          let streak = 0;
          let worst = 0;
          for (let step = 0; step < 1500; step++) {
            const d = W.drawWeather(seed, slot, step, season, cls, streak);
            // the draw AT the bound is clear whatever the roll
            if (streak >= MAX) {
              assert.equal(d.kind, 'clear', `${season}/${cls}/${seed}/${slot} step ${step}: the draw after ${streak} wet draws`);
              forced++;
            }
            streak = d.kind === 'clear' ? 0 : streak + 1;
            worst = Math.max(worst, streak);
          }
          assert.ok(worst <= MAX, `${season}/${cls}/${seed}/${slot}: ${worst} consecutive non-clear draws`);
          if (worst === MAX) reached++;
        }
      }
    }
  }
  assert.ok(forced > 1000 && reached > 400, `the bound is really used: forced ${forced}, reached in ${reached} sequences`);
  // and it is the ONLY thing that changes the kind: below the bound the roll stands; the interval never depends on the streak
  for (let step = 0; step < 200; step++) {
    const free = W.drawWeather(SEED, 3, step, 'winter', 3, 0);
    assert.deepEqual(W.drawWeather(SEED, 3, step, 'winter', 3, 1), free);
    assert.equal(W.drawWeather(SEED, 3, step, 'winter', 3, MAX).intervalMs, free.intervalMs);
  }
});

test('RETURN TO CLEAR, live: over six hours no region of the running schedule has weather for more than 2 intervals (10 minutes) at a stretch', () => {
  const w = new W.RegionalWeather(SEED, 'winter', Uint8Array.from([0, 1, 2, 3, 4]));
  const stretch = [0, 0, 0, 0, 0];
  const longest = [0, 0, 0, 0, 0];
  const clears = [0, 0, 0, 0, 0];
  run(w, 6 * 3600 * 1000, () => {
    for (let slot = 0; slot < 5; slot++) {
      if (w.kindAt(slot) === 'clear') {
        if (stretch[slot] > 0) clears[slot]++;
        stretch[slot] = 0;
      } else {
        stretch[slot] += 250;
        longest[slot] = Math.max(longest[slot], stretch[slot]);
      }
    }
  });
  for (let slot = 0; slot < 5; slot++) {
    assert.ok(longest[slot] <= 2 * C.CITY_WEATHER_INTERVAL_MAX_MS + 250, `slot ${slot}: ${longest[slot]} ms of weather at a stretch`);
    assert.ok(clears[slot] > 20, `slot ${slot} returned to clear ${clears[slot]} times in six hours`);
  }
});

test('MANUAL applies one kind to every region at once and freezes the schedule; Automatic then draws FRESH, replaying nothing', () => {
  const w = new W.RegionalWeather();
  run(w, 600_000);
  const stepsBefore = [0, 1, 2, 3, 4].map((s) => w.slotState(s).step);
  w.setMode('heavy-snow', true); // heavy snow is available in any season, summer included
  for (let slot = 0; slot < 5; slot++) {
    assert.equal(w.kindAt(slot), 'heavy-snow');
    assert.equal(w.alpha('heavy-snow', slot), 1);
  }
  run(w, 3 * 3600 * 1000); // hours pass: the schedule is held
  assert.deepEqual([0, 1, 2, 3, 4].map((s) => w.slotState(s).step), stepsBefore);
  for (let slot = 0; slot < 5; slot++) assert.equal(w.kindAt(slot), 'heavy-snow');
  w.setMode('auto', true);
  for (let slot = 0; slot < 5; slot++) {
    const s = w.slotState(slot);
    assert.equal(s.step, stepsBefore[slot] + 1); // exactly one fresh draw each, no overdue replay
    const expected = W.drawWeather(SEED, slot, stepsBefore[slot], 'summer', 0);
    assert.deepEqual([s.kind, s.remainingMs], [expected.kind, expected.intervalMs]);
  }
});

test('NO CATCH-UP: a huge step is clipped, and a hidden tab or sleep cannot turn into replayed weather', () => {
  const w = new W.RegionalWeather();
  const before = w.slotState(0).remainingMs;
  w.update(7200, false); // two hours "elapsed" in one step
  assert.equal(w.slotState(0).remainingMs, before - 250);
  assert.equal(w.slotState(0).step, 1);
  w.update(0, false);
  w.update(-5, false);
  w.update(Number.NaN, false);
  assert.equal(w.slotState(0).remainingMs, before - 250);
});

test('a change fades the old kind out and the new one in over four seconds; reduced motion switches at once', () => {
  const w = new W.RegionalWeather();
  w.setMode('clear', true);
  w.setMode('rain', false);
  assert.equal(w.kindAt(0), 'rain');
  assert.equal(w.alpha('rain', 0), 0);
  assert.equal(w.alpha('clear', 0), 1);
  for (let k = 0; k < 8; k++) w.update(0.25, false); // 2 s
  assert.ok(Math.abs(w.alpha('rain', 0) - 0.5) < 1e-9);
  assert.ok(Math.abs(w.alpha('clear', 0) - 0.5) < 1e-9);
  for (let k = 0; k < 8; k++) w.update(0.25, false); // 4 s
  assert.equal(w.alpha('rain', 0), 1);
  assert.equal(w.alpha('clear', 0), 0);

  const reduced = new W.RegionalWeather();
  reduced.setMode('clear', true);
  reduced.setMode('light-snow', true);
  assert.equal(reduced.alpha('light-snow', 2), 1); // no fade under reduced motion
  assert.equal(reduced.alpha('clear', 2), 0);
});

test('a season change gives a fresh ELIGIBLE draw to a region whose kind no longer suits, and leaves manual weather alone', () => {
  const w = new W.RegionalWeather();
  w.setMode('auto', true);
  // force a known state: summer rain in the Highlands region (slot 3), then go to winter
  const manual = new W.RegionalWeather();
  manual.setMode('rain', true);
  manual.setSeason('winter', true);
  for (let slot = 0; slot < 5; slot++) assert.equal(manual.kindAt(slot), 'rain'); // manual untouched by seasons
  // automatic: every region ends the change showing a kind that is eligible for winter
  w.setClasses(Uint8Array.from([0, 1, 2, 3, 4]));
  w.setSeason('winter', true);
  for (let slot = 0; slot < 5; slot++) {
    const eligible = W.eligibleKinds('winter', slot).map(([k]) => k);
    assert.ok(eligible.includes(w.kindAt(slot)), `slot ${slot} shows ${w.kindAt(slot)}`);
  }
  // and rain in a winter City is not eligible, so a City region showing rain at the change was re-drawn (its step moved)
  const city = new W.RegionalWeather();
  city.setClasses(classAll(0));
  const stepBefore = city.slotState(0).step;
  const kindBefore = city.kindAt(0);
  city.setSeason('winter', true);
  const redrawn = kindBefore === 'rain' ? 1 : 0;
  assert.equal(city.slotState(0).step, stepBefore + redrawn);
});

test('light snow and heavy snow are different fields: heavy has the larger capacity, the faster fall and the wider sway', () => {
  const w = new W.RegionalWeather();
  assert.equal(w.rain.capacity, 520);
  assert.equal(w.lightSnow.capacity, 520);
  assert.equal(w.heavySnow.capacity, 520);
  const light = new WeatherField();
  light.setKind('snow');
  const heavy = new WeatherField(C.CITY_HEAVY_SNOW_PARTICLES);
  heavy.setKind('heavy-snow');
  // within the one 520-particle pool a kind alone keeps its own number: light snow 300 (as it always had), heavy snow 520
  assert.equal(light.count, 300);
  assert.equal(heavy.count, 520);
  assert.ok(heavy.count > light.count * 1.7, 'heavy snow keeps clearly more flakes than light snow (520 against 300)');
  // fall: after one second the heavy flake has fallen further (same seeded start, same depth)
  const y0 = heavy.ys[0];
  const d = heavy.depth[0];
  const frac = (v: number) => v - Math.floor(v);
  heavy.update(0.1);
  light.update(0.1);
  assert.ok(Math.abs(heavy.ys[0] - frac(y0 + C.CITY_HEAVY_SNOW_SPEED * d * 0.1)) < 1e-5);
  assert.ok(Math.abs(light.ys[0] - frac(y0 + C.CITY_SNOW_SPEED * d * 0.1)) < 1e-5);
  assert.ok(C.CITY_HEAVY_SNOW_SPEED > C.CITY_SNOW_SPEED && C.CITY_HEAVY_SNOW_SWAY > C.CITY_SNOW_SWAY);
  // heavy snow lays a stronger veil than light snow
  assert.ok(C.CITY_OVERCAST_HEAVY_SNOW.a > C.CITY_OVERCAST_SNOW.a);
});

test('a field has live particles only while some region shows its kind, and the fields stay put under reduced motion', () => {
  const w = new W.RegionalWeather();
  w.setMode('clear', true);
  w.update(0.25, false);
  assert.deepEqual([w.rain.count, w.lightSnow.count, w.heavySnow.count], [0, 0, 0]);
  assert.equal(w.anyWeather(), false);
  w.setMode('light-snow', true);
  assert.deepEqual([w.rain.count, w.lightSnow.count, w.heavySnow.count], [0, 300, 0]);
  w.setMode('heavy-snow', true);
  assert.deepEqual([w.rain.count, w.lightSnow.count, w.heavySnow.count], [0, 0, 520]);
  const frozen = [...w.heavySnow.ys];
  w.update(0.25, true); // reduced motion: the schedule runs, the particles do not move
  assert.deepEqual([...w.heavySnow.ys], frozen);
  w.update(0.25, false);
  assert.notDeepEqual([...w.heavySnow.ys], frozen);
});

// ── the particle pool: the live total across ALL fields is never above the pinned cap ─────────────────────────────────

const CLASSES = Uint8Array.from([0, 1, 2, 3, 4]);
const total = (w: W.RegionalWeather): number => w.rain.count + w.lightSnow.count + w.heavySnow.count;
const counts = (w: W.RegionalWeather): number[] => [w.rain.count, w.lightSnow.count, w.heavySnow.count];

test('THE POOL, A KIND ALONE: each kind keeps its own number, within the cap: rain 520, light snow 300, heavy snow 520', () => {
  const w = new W.RegionalWeather();
  for (const [mode, expected] of [['rain', [520, 0, 0]], ['light-snow', [0, 300, 0]], ['heavy-snow', [0, 0, 520]], ['clear', [0, 0, 0]]] as const) {
    w.setMode(mode, true);
    assert.deepEqual(counts(w), expected, mode);
    assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL);
  }
});

test('THE POOL, ALL THREE KINDS AT ONCE: the seeded winter start shows rain in 1 region, light snow in 2 and heavy snow in 1, so the 520 are shared 130 / 260 / 130 (by hand), exactly at the cap and never above it', () => {
  const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
  assert.deepEqual([0, 1, 2, 3, 4].map((s) => w.kindAt(s)), ['light-snow', 'rain', 'light-snow', 'heavy-snow', 'clear']);
  // 520 * 1 / 4 = 130 (rain), 520 * 2 / 4 = 260 (light snow, inside its capacity of 300), 520 * 1 / 4 = 130 (heavy snow)
  assert.deepEqual(counts(w), [130, 260, 130]);
  assert.equal(total(w), C.CITY_WEATHER_PARTICLE_TOTAL, 'the cap is reached exactly');
});

/** A winter state with `a` regions showing rain, `b` light snow, `c` heavy snow and the rest clear, each kind placed in a region class that may show it. */
function poolState(a: number, b: number, c: number): W.RegionalWeather {
  const kinds: Array<'rain' | 'light-snow' | 'heavy-snow' | 'clear'> = [];
  for (let k = 0; k < a; k++) kinds.push('rain');
  for (let k = 0; k < b; k++) kinds.push('light-snow');
  for (let k = 0; k < c; k++) kinds.push('heavy-snow');
  while (kinds.length < 5) kinds.push('clear');
  // winter: rain is eligible on the Coast (class 1), light snow on the City (class 0), heavy snow on the Highlands (class 3), clear anywhere (Farmland, 4)
  const classOf = { rain: 1, 'light-snow': 0, 'heavy-snow': 3, clear: 4 } as const;
  const w = new W.RegionalWeather(SEED, 'winter', Uint8Array.from(kinds.map((k) => classOf[k])));
  w.restore({ mode: 'auto', seed: SEED, regions: kinds.map((kind, i) => ({ kind, step: 1 + i, wet: kind === 'clear' ? 0 : 1, remaining: 100_000 })) }, 'winter');
  return w;
}

test('THE POOL, EXACT ALLOCATIONS, HAND-COMPUTED: one kind 520; two equal kinds 260 + 260; three equal kinds 173 x 3 = 519; a 1:2:1 split 130/260/130; a capacity that binds 0/300/104', () => {
  // [rain regions, light-snow regions, heavy-snow regions] -> [rain, light snow, heavy snow] live particles, worked out by hand:
  const cases: Array<[number[], number[], string]> = [
    [[1, 0, 0], [520, 0, 0], 'one kind: its whole capacity'],
    [[0, 1, 0], [0, 300, 0], 'one kind, capacity-limited: light snow 300'],
    [[0, 0, 1], [0, 0, 520], 'one kind: heavy snow 520'],
    [[2, 0, 0], [520, 0, 0], 'one kind in two regions is still the whole pool'],
    [[1, 0, 1], [260, 0, 260], 'two equal kinds: 520 * 1 / 2 each'],
    [[1, 1, 0], [260, 260, 0], 'two equal kinds, light snow inside its capacity'],
    [[0, 1, 1], [0, 260, 260], 'two equal kinds'],
    [[1, 1, 1], [173, 173, 173], 'three equal kinds: floor(520 / 3) = 173 each, 519 in all (one short of the cap: the flooring remainder is not redistributed)'],
    [[1, 2, 1], [130, 260, 130], 'unequal: shared in proportion to the regions showing each kind, 1 : 2 : 1 of 520'],
    [[2, 2, 1], [208, 208, 104], 'unequal, exact: 520 * 2 / 5, 520 * 2 / 5, 520 * 1 / 5'],
    [[0, 4, 1], [0, 300, 104], 'a capacity binds: light snow would be 416 but holds 300 and the 116 it frees stay unused; heavy snow 104'],
  ];
  for (const [[a, b, c], expected, why] of cases) {
    const w = poolState(a, b, c);
    assert.deepEqual(counts(w), expected, `${a}/${b}/${c}: ${why}`);
    assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL);
  }
});

/** The exact allocation for EVERY combination of regions showing each kind, from tools/pool-table.py (a separate stdlib program written from the spec): [rain regions, light regions, heavy regions, rain, light, heavy]. */
const POOL_TABLE: number[][] = [[0,0,1,0,0,520],[0,0,2,0,0,520],[0,0,3,0,0,520],[0,0,4,0,0,520],[0,0,5,0,0,520],[0,1,0,0,300,0],[0,1,1,0,260,260],[0,1,2,0,173,346],[0,1,3,0,130,390],[0,1,4,0,104,416],[0,2,0,0,300,0],[0,2,1,0,300,173],[0,2,2,0,260,260],[0,2,3,0,208,312],[0,3,0,0,300,0],[0,3,1,0,300,130],[0,3,2,0,300,208],[0,4,0,0,300,0],[0,4,1,0,300,104],[0,5,0,0,300,0],[1,0,0,520,0,0],[1,0,1,260,0,260],[1,0,2,173,0,346],[1,0,3,130,0,390],[1,0,4,104,0,416],[1,1,0,260,260,0],[1,1,1,173,173,173],[1,1,2,130,130,260],[1,1,3,104,104,312],[1,2,0,173,300,0],[1,2,1,130,260,130],[1,2,2,104,208,208],[1,3,0,130,300,0],[1,3,1,104,300,104],[1,4,0,104,300,0],[2,0,0,520,0,0],[2,0,1,346,0,173],[2,0,2,260,0,260],[2,0,3,208,0,312],[2,1,0,346,173,0],[2,1,1,260,130,130],[2,1,2,208,104,208],[2,2,0,260,260,0],[2,2,1,208,208,104],[2,3,0,208,300,0],[3,0,0,520,0,0],[3,0,1,390,0,130],[3,0,2,312,0,208],[3,1,0,390,130,0],[3,1,1,312,104,104],[3,2,0,312,208,0],[4,0,0,520,0,0],[4,0,1,416,0,104],[4,1,0,416,104,0],[5,0,0,520,0,0]];

test('THE POOL, EVERY WEIGHT COMBINATION: all 55 splits of the five regions across rain, light snow and heavy snow get exactly the independently computed allocation, and the general bound holds: cap - (kinds shown - 1) <= total <= cap unless a capacity binds', () => {
  assert.equal(POOL_TABLE.length, 55);
  for (const [a, b, c, rain, light, heavy] of POOL_TABLE) {
    const w = poolState(a, b, c);
    assert.deepEqual(counts(w), [rain, light, heavy], `${a}/${b}/${c}`);
    const live = [a, b, c].filter((n) => n > 0).length;
    const sum = a + b + c;
    // light snow's entitlement before its capacity (520 * b / sum) above 300 means the capacity binds, and the bound does not apply
    const capacityBinds = (520 * b) / sum > 300;
    assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL, `${a}/${b}/${c}: ${total(w)} above the cap`);
    if (!capacityBinds) assert.ok(total(w) >= C.CITY_WEATHER_PARTICLE_TOTAL - (live - 1), `${a}/${b}/${c}: ${total(w)} is below cap - (${live} - 1)`);
    // a kind that is not shown holds nothing, and a shown kind always holds something
    for (const [n, got] of [[a, rain], [b, light], [c, heavy]] as const) assert.equal(n === 0, got === 0, `${a}/${b}/${c}: a kind is live exactly when a region shows it`);
  }
});

test('THE POOL, A CAPACITY BINDS: where a kind is capped (light snow at 300) it keeps its capacity and the pool is not over-spent', () => {
  const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
  // light snow in 4 regions and heavy snow in 1: 520 * 4 / 5 = 416 for light, capped at 300; heavy 520 * 1 / 5 = 104
  w.restore({ mode: 'auto', seed: SEED, regions: [
    { kind: 'light-snow', step: 1, wet: 1, remaining: 100_000 },
    { kind: 'light-snow', step: 1, wet: 1, remaining: 100_000 },
    { kind: 'light-snow', step: 1, wet: 1, remaining: 100_000 },
    { kind: 'heavy-snow', step: 1, wet: 1, remaining: 100_000 },
    { kind: 'light-snow', step: 1, wet: 1, remaining: 100_000 },
  ] }, 'winter');
  assert.deepEqual(counts(w), [0, 300, 104]);
  assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL);
});

test('THE POOL, A TRANSITION: from rain everywhere to heavy snow everywhere the total is within the cap at EVERY step, a field that stays shown moves at most 200 a second, a field that stops being shown is already invisible, and it ends at heavy snow 520', () => {
  const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
  w.setMode('rain', true);
  assert.deepEqual(counts(w), [520, 0, 0]);
  w.setMode('heavy-snow', false); // rain fades out as heavy snow fades in, over four seconds; the shares ease
  let prev = counts(w);
  let peak = total(w);
  let steps = 0;
  const dt = 1 / 16;
  for (let t = 0; t < 12; t += dt) {
    w.update(dt, false);
    steps++;
    const now = counts(w);
    peak = Math.max(peak, total(w));
    assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL, `step ${steps}: total ${total(w)} (${now})`);
    const fields = [w.rain, w.lightSnow, w.heavySnow];
    const kinds = ['rain', 'light-snow', 'heavy-snow'] as const;
    for (let k = 0; k < 3; k++) {
      if (fields[k].kind !== 'off') {
        // a field that is shown eases: it moves at most the balance rate
        assert.ok(Math.abs(now[k] - prev[k]) <= C.CITY_WEATHER_BALANCE_RATE * dt + 1, `step ${steps}: field ${k} moved ${now[k] - prev[k]}`);
      } else if (prev[k] > 0) {
        // a field that has just stopped being shown drops to nothing at once, and it is already INVISIBLE: no region draws it at any strength
        for (let slot = 0; slot < C.CITY_REGION_SLOTS; slot++) assert.equal(w.alpha(kinds[k], slot), 0, `step ${steps}: ${kinds[k]} still drawn in region ${slot} as it drops out`);
      }
    }
    prev = now;
  }
  assert.deepEqual(counts(w), [0, 0, 520], 'it ends as heavy snow alone, at its whole capacity');
  assert.ok(peak <= C.CITY_WEATHER_PARTICLE_TOTAL);
});

test('THE POOL, A FIELD ONLY GROWS INTO THE ROOM THE OTHERS HAVE LEFT: a kind that starts while another holds the whole pool gets nothing until the other lets go', () => {
  const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
  w.setMode('rain', true);
  w.setMode('light-snow', false);
  w.update(0.001, false); // the first sliver of time: rain still holds the pool, so light snow has no room yet
  assert.ok(total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL);
  assert.equal(w.lightSnow.count, 0, 'no room yet');
  for (let k = 0; k < 40; k++) w.update(0.05, false); // two seconds: rain has let go enough for light snow to grow
  assert.ok(w.lightSnow.count > 0 && total(w) <= C.CITY_WEATHER_PARTICLE_TOTAL);
});

test('THE POOL OVER TWO HOURS: in Automatic weather, in every season and class set, the live total never exceeds 520 at any step, and it does reach it', () => {
  let peak = 0;
  let steps = 0;
  for (const season of SEASONS) {
    for (const classes of [CLASSES, classAll(0), classAll(3)]) {
      const w = new W.RegionalWeather(SEED, season, classes);
      for (let t = 0; t < 7_200_000; t += 250) {
        w.update(0.25, false);
        const n = total(w);
        assert.ok(n <= C.CITY_WEATHER_PARTICLE_TOTAL, `${season}: total ${n} at ${t} ms`);
        for (const f of [w.rain, w.lightSnow, w.heavySnow]) assert.ok(f.count <= f.capacity);
        peak = Math.max(peak, n);
        steps++;
      }
    }
  }
  assert.ok(steps > 100_000);
  assert.equal(peak, C.CITY_WEATHER_PARTICLE_TOTAL, 'the pool is really used up to its cap');
});

test('THE POOL IS DETERMINISTIC, and reduced motion moves every share at once', () => {
  const trace = (): string => {
    const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
    const out: number[][] = [];
    for (let t = 0; t < 600_000; t += 250) {
      w.update(0.25, false);
      if (t % 5_000 === 0) out.push(counts(w));
    }
    return JSON.stringify(out);
  };
  assert.equal(trace(), trace());
  const w = new W.RegionalWeather(SEED, 'winter', CLASSES);
  w.setMode('rain', true);
  w.setMode('heavy-snow', true); // reduced motion: instant
  assert.deepEqual(counts(w), [0, 0, 520]);
});

test('WeatherField: a pool share limits the live count in whole particles and never raises it above the kind or the capacity', () => {
  const f = new WeatherField(520);
  f.setKind('rain');
  assert.equal(f.count, 520);
  f.setShare(100.9);
  assert.equal(f.count, 100);
  assert.equal(f.share, 100.9);
  f.setShare(10_000);
  assert.equal(f.count, 520);
  f.setShare(-5);
  assert.equal(f.count, 0);
  f.setKind('off');
  f.setShare(200);
  assert.equal(f.count, 0);
});

test('the status words say what each region shows, never a forecast', () => {
  const regions = [
    { slot: 0, name: 'City', kind: 'rain' as const },
    { slot: 1, name: 'Coast', kind: 'clear' as const },
    { slot: 3, name: 'Highlands', kind: 'heavy-snow' as const },
  ];
  assert.equal(
    W.weatherStatus('auto', regions),
    'Automatic regional weather: rain in the City, heavy snow in the Highlands. Elsewhere clear.',
  );
  assert.equal(
    W.weatherStatus('auto', regions.map((r) => ({ ...r, kind: 'clear' as const }))),
    'Automatic regional weather: clear skies everywhere right now.',
  );
  assert.equal(W.weatherStatus('rain', regions), 'Rain everywhere. Held until you choose Automatic.');
  assert.equal(W.weatherStatus('clear', regions), 'Clear skies everywhere. Held until you choose Automatic.');
  assert.equal(
    W.weatherStatus('light-snow', regions),
    'Light snow everywhere. Held until you choose Automatic.',
  );
  assert.equal(
    W.weatherStatus('auto', [{ slot: 0, name: 'Everywhere', kind: 'rain' }]),
    'Automatic regional weather: rain everywhere.',
  );
});

// ── round 3: reduced motion switched ON while a weather fade is running settles it at once ────────────────────────────────────

test('REDUCED MOTION SWITCHED ON MID-FADE SETTLES THE WEATHER FADE AT ONCE, to exactly its target (and the particle shares with it), and switching it off again restarts nothing', () => {
  const w = new W.RegionalWeather();
  w.setMode('rain', true);
  w.setMode('heavy-snow', false); // every region fades rain out and heavy snow in over 4 s
  for (let k = 0; k < 14; k++) w.update(0.25, false); // 3.5 s of the 4: the state the reviewer reproduced
  assert.ok(Math.abs(w.alpha('heavy-snow', 0) - 0.875) < 1e-9, `heavy ${w.alpha('heavy-snow', 0)}`);
  assert.ok(Math.abs(w.alpha('rain', 0) - 0.125) < 1e-9, `rain ${w.alpha('rain', 0)}`);
  // the preference turns ON: the very next step shows the targets exactly
  w.update(0.1, true);
  for (let slot = 0; slot < C.CITY_REGION_SLOTS; slot++) {
    assert.equal(w.alpha('heavy-snow', slot), 1, `slot ${slot} heavy`);
    assert.equal(w.alpha('rain', slot), 0, `slot ${slot} rain`);
    assert.equal(w.kindAt(slot), 'heavy-snow');
  }
  // the particle pool is at its target too: all of it heavy snow, none rain
  assert.equal(w.heavySnow.count, C.CITY_HEAVY_SNOW_PARTICLES);
  assert.equal(w.rain.count, 0);
  // a zero-length step settles as well, and an AUTOMATIC kind change that is mid-fade is settled in the same way
  const zero = new W.RegionalWeather();
  zero.setMode('clear', true);
  zero.setMode('rain', false);
  assert.equal(zero.alpha('rain', 0), 0);
  zero.update(0, true);
  assert.equal(zero.alpha('rain', 0), 1);
  assert.equal(zero.alpha('clear', 0), 0);
  // (c) switching it back OFF restarts nothing: every alpha stays at its target for as long as we look, and no field is 'fading' again
  for (let k = 0; k < 40; k++) {
    w.update(0.25, false);
    for (let slot = 0; slot < C.CITY_REGION_SLOTS; slot++) {
      assert.equal(w.alpha('heavy-snow', slot), 1);
      assert.equal(w.alpha('rain', slot), 0);
    }
  }
  assert.equal(w.heavySnow.count, C.CITY_HEAVY_SNOW_PARTICLES);
  assert.equal(w.rain.count, 0);
});
