/**
 * HIDDEN-VIEW RESUME (M3 S4): the game loop advances NOTHING while the document is hidden, even when
 * the platform keeps delivering animation frames, and the first frame back starts a fresh delta. The
 * bounded catch-up then covers the hidden interval EXACTLY ONCE: it subtracts any time that did
 * advance while hidden, and never exceeds AMBIENT_CATCHUP_MAX_SEC.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { AMBIENT_CATCHUP_MAX_SEC, AMBIENT_CATCHUP_STEP_SEC, AMBIENT_PATH_SEARCHES_PER_TICK, MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST } from '../src/constants.ts';
import type { FrameClock } from '../src/office/engine/frameStep.ts';
import { documentHidden, nextFrameDelta } from '../src/office/engine/frameStep.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

beforeAll(() => loadBundledCatalog());

test('the loop advances nothing while hidden, even if frames keep arriving; the first frame back starts fresh', () => {
  // the exact per-frame decision gameLoop.ts makes (frameStep.ts), driven frame by frame
  const clock: FrameClock = { lastTime: 0 };
  let now = 0;
  let hidden = false;
  const dts: number[] = [];
  const frame = (ms: number) => {
    now += ms;
    const dt = nextFrameDelta(clock, now, hidden, MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST);
    if (dt !== null) dts.push(dt);
  };
  frame(16);
  frame(16);
  frame(16);
  assert.equal(dts.length, 3);
  hidden = true;
  for (let k = 0; k < 120; k++) frame(500); // a throttled tab still delivering frames: 60 s
  assert.equal(dts.length, 3, 'no frame advanced while hidden');
  hidden = false;
  frame(16);
  frame(16);
  assert.equal(dts[3], 0, 'the first frame back carries no hidden time');
  assert.ok(dts[4] <= MAX_DELTA_TIME_SEC);
  // the loop reads visibility through documentHidden(): no document means never hidden
  assert.equal(documentHidden(), false);
});

const living = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));

test('resume covers the hidden interval exactly once and stays bounded', () => {
  const o = new OfficeState(living());
  o.setAmbientEnabled(true);
  for (let k = 0; k < 30; k++) o.update(0.1);
  // (a) the guarded case: nothing advanced while hidden, so 8 s hidden -> 8 s caught up, once
  let clockAtHide = o.ambientClock;
  let advanced = o.catchUpAmbient(8, o.ambientClock - clockAtHide);
  assert.ok(Math.abs(advanced - 8) < 1e-9);
  assert.ok(Math.abs(o.ambientClock - clockAtHide - 8) < 1e-9, 'the hidden 8 s advanced exactly once');
  // (b) frames DID run while hidden (3 s of them): only the remaining 5 s is caught up
  clockAtHide = o.ambientClock;
  for (let k = 0; k < 30; k++) o.update(0.1);
  advanced = o.catchUpAmbient(8, o.ambientClock - clockAtHide);
  assert.ok(Math.abs(advanced - 5) < 1e-6, `${advanced}`);
  assert.ok(Math.abs(o.ambientClock - clockAtHide - 8) < 1e-6, 'the hidden 8 s advanced exactly once in total');
  // (c) more advanced while hidden than the interval: nothing more
  clockAtHide = o.ambientClock;
  for (let k = 0; k < 50; k++) o.update(0.1);
  assert.equal(o.catchUpAmbient(3, o.ambientClock - clockAtHide), 0);
  // (d) an hour hidden: bounded
  advanced = o.catchUpAmbient(3600, 0);
  assert.ok(advanced <= AMBIENT_CATCHUP_MAX_SEC + 1e-9 && advanced > 0);
});

test('one resume advances at most AMBIENT_CATCHUP_MAX_SEC in whole steps: <= 200 steps and <= 800 path searches (4 per step)', () => {
  const o = new OfficeState(living());
  o.setAmbientEnabled(true);
  for (let k = 0; k < 30; k++) o.update(0.1);
  const w = o.ambient!;
  const steps = Math.round(AMBIENT_CATCHUP_MAX_SEC / AMBIENT_CATCHUP_STEP_SEC);
  assert.equal(steps, 200, 'the step cap the budget row states');
  const searches0 = w.stats.searches;
  const advanced = o.catchUpAmbient(3600, 0); // an hour hidden: far beyond the cap
  assert.ok(Math.abs(advanced - AMBIENT_CATCHUP_MAX_SEC) < 1e-9, `advanced ${advanced} s, the cap exactly`);
  const searched = w.stats.searches - searches0;
  assert.ok(searched <= steps * AMBIENT_PATH_SEARCHES_PER_TICK, `searches ${searched} <= ${steps * AMBIENT_PATH_SEARCHES_PER_TICK}`);
  console.log(`[resume] ${JSON.stringify({ advancedSec: advanced, steps, searches: searched, searchCap: steps * AMBIENT_PATH_SEARCHES_PER_TICK })}`);
});

test('reduced motion holds the hidden-view catch-up ENTIRELY: nothing is advanced and nobody moves', () => {
  const o = new OfficeState(living());
  o.setAmbientEnabled(true);
  for (let k = 0; k < 30; k++) o.update(0.1);
  o.reducedMotion = true;
  const clock = o.ambientClock;
  const at = () =>
    o
      .getAmbientResidents()
      .map((r) => `${r.id}@${r.x},${r.y}`)
      .join('|');
  const before = at();
  assert.equal(o.catchUpAmbient(15, 0), 0, 'advances 0 s');
  assert.equal(o.ambientClock, clock, 'the ambient clock did not move');
  assert.equal(at(), before, 'no resident moved');
});
