/**
 * THE TICK-RATE BOUND (M3 S7): the game loop's own per-frame decision (frameStep.ts, exactly what
 * gameLoop.ts calls) driven by animation frames at 60, 75, 120, 144 and 240 Hz for 10 s, with the REAL
 * OfficeState.update on each tick. In EVERY 1-second window of wall time: ticks <= 1 / interval + burst,
 * and path searches <= AMBIENT_PATH_SEARCHES_PER_TICK per tick. Simulated time keeps pace with wall
 * time; a 60 Hz display with early- and late-stamped frames still ticks on every frame; rapid
 * visibility flips cannot raise the rate; the hidden guard still holds.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { AMBIENT_PATH_SEARCHES_PER_TICK, MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST } from '../src/constants.ts';
import type { FrameClock } from '../src/office/engine/frameStep.ts';
import { nextFrameDelta } from '../src/office/engine/frameStep.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

let os: OfficeState;
beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
  os.setAmbientEnabled(true);
}, 300_000);

/** The most ticks any 1-second window may hold. */
const WINDOW_MAX_TICKS = 1 / SIM_MIN_TICK_INTERVAL_SEC + SIM_TICK_BURST;
const step = (clock: FrameClock, t: number, hidden = false) => nextFrameDelta(clock, t, hidden, MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST);

/** Drive frames at the given times (ms); each tick runs the real update. Per tick: [time, searches]. */
function drive(times: number[]) {
  const clock: FrameClock = { lastTime: 0 };
  const ticks: Array<[number, number]> = [];
  let simulated = 0;
  for (const t of times) {
    const dt = step(clock, t);
    if (dt === null) continue;
    const before = os.ambient!.stats.searches;
    os.update(dt);
    simulated += dt;
    ticks.push([t, os.ambient!.stats.searches - before]);
  }
  return { ticks, simulated };
}

/** The busiest 1-second window: [ticks, searches]. */
function busiestSecond(ticks: Array<[number, number]>) {
  let worstTicks = 0;
  let worstSearches = 0;
  let j = 0;
  let searches = 0;
  for (let i = 0; i < ticks.length; i++) {
    searches += ticks[i][1];
    while (ticks[i][0] - ticks[j][0] >= 1000) searches -= ticks[j++][1];
    worstTicks = Math.max(worstTicks, i - j + 1);
    worstSearches = Math.max(worstSearches, searches);
  }
  return [worstTicks, worstSearches];
}

test('at 60, 75, 120, 144 and 240 Hz no 1-second window exceeds the tick or search bound, and simulated time keeps pace', () => {
  const rows = [];
  for (const hz of [60, 75, 120, 144, 240]) {
    const times = Array.from({ length: 10 * hz }, (_, k) => 1000 + (k * 1000) / hz);
    const { ticks, simulated } = drive(times);
    const [worstTicks, worstSearches] = busiestSecond(ticks);
    assert.ok(worstTicks <= WINDOW_MAX_TICKS, `${hz} Hz: ${worstTicks} ticks in one second`);
    assert.ok(worstSearches <= AMBIENT_PATH_SEARCHES_PER_TICK * worstTicks, `${hz} Hz: ${worstSearches} searches in one second`);
    for (const [, n] of ticks) assert.ok(n <= AMBIENT_PATH_SEARCHES_PER_TICK, `${hz} Hz: ${n} searches in one tick`);
    assert.ok(Math.abs(simulated - (times.at(-1)! - times[0]) / 1000) < 0.05, `${hz} Hz: simulated ${simulated} s`);
    rows.push({ hz, frames: times.length, ticks: ticks.length, busiestSecondTicks: worstTicks, busiestSecondSearches: worstSearches });
  }
  assert.equal(rows[0].ticks, rows[0].frames, 'a 60 Hz display ticks on every frame');
  for (const r of rows.slice(1)) assert.ok(r.ticks < r.frames, `${r.hz} Hz is held below its frame rate`);
  console.log(`[S7] ${JSON.stringify({ tickRate: rows, windowMaxTicks: Math.round(WINDOW_MAX_TICKS * 10) / 10, windowMaxSearches: Math.round(AMBIENT_PATH_SEARCHES_PER_TICK * WINDOW_MAX_TICKS * 10) / 10 })}`);
}, 300_000);

test('a 60 Hz display whose frames are stamped early and late (14.5 / 18.8 ms) still ticks on every frame', () => {
  const clock: FrameClock = { lastTime: 0 };
  let t = 1000;
  let ticked = 0;
  for (let k = 0; k < 600; k++) {
    t += k % 2 ? 18.83 : 14.5;
    if (step(clock, t) !== null) ticked++;
  }
  assert.equal(ticked, 600);
});

test('visibility flipping on every frame at 240 Hz cannot raise the tick rate', () => {
  const clock: FrameClock = { lastTime: 0 };
  const ticks: Array<[number, number]> = [];
  for (let k = 0; k < 2400; k++) {
    const t = 1000 + (k * 1000) / 240;
    if (step(clock, t, k % 2 === 1) !== null) ticks.push([t, 0]);
  }
  assert.ok(busiestSecond(ticks)[0] <= WINDOW_MAX_TICKS);
});

/**
 * WHAT C1 ESTABLISHED, RE-CHECKED UNDER THE LIMITER (the S8a C1 captures ran on a superseded candidate):
 * 8 real agents assigned seats, frames at 144 Hz through the loop's own frame step for 60 s, sampled
 * every 250 ms of frame time as the C1 capture samples. Residents move, ids stay unique, no two agents
 * share a seat and no resident holds one, and no resident stays on an agent's tile or an assigned
 * seat's zone longer than the 1500 ms grace. Then a constructed conflict (a resident parked beside a seat
 * that an agent is then assigned) is resolved by a yield, also under the limiter.
 */
test('under the limiter at 144 Hz: residents move, ids unique, no seat conflicts, no violations over the grace', () => {
  const o = new OfficeState(JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
  o.setAmbientEnabled(true);
  const w = o.ambient!;
  const cols = o.layout.cols;
  const free = [...o.seats].filter(([, st]) => !st.assigned).slice(0, 8);
  assert.equal(free.length, 8, 'precondition: 8 free seats');
  free.forEach(([uid], k) => o.addAgent(100 + k, undefined, undefined, uid, true));
  const seatIds = [...o.characters.values()].map((c) => c.seatId);
  assert.equal(new Set(seatIds).size, 8, 'precondition: 8 agents on 8 distinct seats');
  const assignedSeats = free.map(([uid]) => uid);
  const ids = w.residents.map((r) => r.id);
  const start = new Map(w.residents.map((r) => [r.id, r.tileRow * cols + r.tileCol]));
  const moved = new Set<string>();
  const tilesSeen = new Map<string, Set<number>>(w.residents.map((r) => [r.id, new Set([r.tileRow * cols + r.tileCol])]));
  const inZoneSince = new Map<string, number>();
  let violations = 0;
  let zoneSamples = 0;
  let samples = 0;
  const clock: FrameClock = { lastTime: 0 };
  let nextSample = 1000;
  for (let k = 0; k < 60 * 144; k++) {
    const t = 1000 + (k * 1000) / 144;
    const dt = step(clock, t);
    if (dt !== null) o.update(dt);
    if (t < nextSample) continue;
    nextSample += 250;
    samples++;
    const now = w.residents.map((r) => r.id);
    assert.equal(new Set(now).size, now.length, 'ids unique');
    assert.deepEqual([...now].sort(), [...ids].sort(), 'the same residents throughout');
    const agentTiles = new Set([...o.characters.values()].map((c) => c.tileRow * cols + c.tileCol));
    const zone = w.priorityTiles({ agentTiles, assignedSeats });
    for (const r of w.residents) {
      const here = r.tileRow * cols + r.tileCol;
      if (here !== start.get(r.id)) moved.add(r.id);
      tilesSeen.get(r.id)!.add(here);
      if (zone.has(here)) {
        zoneSamples++;
        const since = inZoneSince.get(r.id) ?? t;
        inZoneSince.set(r.id, since);
        if (t - since > 1500) violations++;
      } else inZoneSince.delete(r.id);
    }
    const seats = [...o.characters.values()].map((c) => c.seatId);
    assert.equal(new Set(seats).size, seats.length, 'no two agents share a seat');
  }
  assert.deepEqual(Object.keys(o.getPersistableSeats()).map(Number).sort(), free.map((_, k) => 100 + k), 'only the agents hold seats');
  assert.equal(violations, 0, 'no resident held an agent tile or seat zone past the grace');
  const threePlus = [...tilesSeen.values()].filter((v) => v.size >= 3).length;
  assert.ok(moved.size >= 0.9 * ids.length, `moved ${moved.size}/${ids.length}`);
  console.log(`[S7] ${JSON.stringify({ c1UnderLimiter: { hz: 144, seconds: 60, samples, residents: ids.length, moved: moved.size, visited3PlusTiles: threePlus, agents: 8, zoneSamples, violationsOverGrace: violations } })}`);
}, 600_000);

test('under the limiter at 144 Hz a constructed seat conflict is resolved by a yield (with a no-agent control)', () => {
  const run = (assignAgent: boolean) => {
    const o = new OfficeState(JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
    o.setAmbientEnabled(true);
    const w = o.ambient!;
    const cols = o.layout.cols;
    const [uid, seat] = [...o.seats].find(([, st]) => !st.assigned)!;
    const nb = [[0, -1], [0, 1], [-1, 0], [1, 0]].map(([dc, dr]) => [seat.seatCol + dc, seat.seatRow + dr]).find(([c, r]) => o.tileMap[r]?.[c] !== undefined && !o.blockedTiles.has(`${c},${r}`) && w.pathfinder.isWalkable(c, r));
    assert.ok(nb, 'precondition: a walkable seat neighbour');
    const r = w.residents.find((x) => x.district === 'Palace')!;
    Object.assign(r, { tileCol: nb[0], tileRow: nb[1], x: nb[0] * 16 + 8, y: nb[1] * 16 + 8, state: 'dwell', timer: 100 });
    const tile = nb[1] * cols + nb[0];
    assert.ok(w.priorityTiles({ agentTiles: new Set(), assignedSeats: [uid] }).has(tile), 'precondition: the resident stands in the seat zone');
    if (assignAgent) o.addAgent(1, undefined, undefined, uid, true);
    const clock: FrameClock = { lastTime: 0 };
    for (let k = 0; k < 3 * 144; k++) {
      const dt = step(clock, 1000 + (k * 1000) / 144);
      if (dt !== null) o.update(dt);
    }
    return r.tileRow * cols + r.tileCol !== tile;
  };
  assert.equal(run(false), false, 'control: no agent, the parked resident stays');
  assert.equal(run(true), true, 'agent assigned: the resident yields and leaves');
}, 600_000);

test('the hidden guard still holds under the bound: nothing advances while hidden, the first frame back carries no hidden time', () => {
  const clock: FrameClock = { lastTime: 0 };
  let t = 1000;
  const at = (ms: number, hidden = false) => step(clock, (t += ms), hidden);
  assert.equal(at(7), 0, 'the first frame ticks, fresh');
  assert.equal(at(7), null, '7 ms later: less than one credit, no tick');
  assert.equal(at(7), null, '14 ms later: still less than one credit');
  const carried = at(7);
  assert.ok(carried !== null && Math.abs(carried - 0.021) < 1e-9, 'at 21 ms it ticks, carrying all 21 ms (no frame time lost)');
  for (let k = 0; k < 100; k++) assert.equal(at(500, true), null);
  assert.equal(at(16), 0, 'first frame back: fresh');
});
