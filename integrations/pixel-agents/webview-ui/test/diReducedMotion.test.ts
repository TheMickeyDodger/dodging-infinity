/**
 * REDUCED MOTION (DI mode only): motion removed, never information. Spawn/despawn SETTLE in
 * the same tick while additions and removals still apply; nothing moves on its own; activity
 * flags are never touched; with the flag off (normal Pixel) behaviour is unchanged.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import { createCharacter, settleCharacter } from '../src/office/engine/characters.js';
import { OfficeState } from '../src/office/engine/officeState.js';
import type { PreferenceQuery } from '../src/office/reducedMotion.js';
import { subscribeReducedMotion } from '../src/office/reducedMotion.js';
import type { OfficeLayout, Seat } from '../src/office/types.js';
import {
  CharacterState,
  Direction,
  MATRIX_EFFECT_DURATION,
  TileType,
} from '../src/office/types.js';

const FRAME = 1 / 60;

/** All-floor layout, no furniture — no catalog needed, every tile walkable. */
function floorLayout(cols = 9, rows = 7): OfficeLayout {
  return {
    version: 1,
    cols,
    rows,
    tiles: new Array<TileType>(cols * rows).fill(TileType.FLOOR_1),
    furniture: [],
  };
}

test('reduced: a spawn is visible in the same tick; a despawn is gone after ONE update', () => {
  const os = new OfficeState(floorLayout());
  os.reducedMotion = true;
  os.addAgent(1);
  assert.equal(os.characters.get(1)?.matrixEffect, null); // revealed at once, never mid-spawn
  os.removeAgent(1);
  assert.ok(os.characters.has(1)); // the removal goes through the canonical completion path…
  os.update(FRAME);
  assert.equal(os.characters.has(1), false); // …and lands on the very next update
});

test('flag off (normal Pixel): spawn and despawn still animate exactly as before', () => {
  const os = new OfficeState(floorLayout());
  os.addAgent(1);
  assert.equal(os.characters.get(1)?.matrixEffect, 'spawn');
  os.removeAgent(1);
  os.update(FRAME);
  assert.equal(os.characters.get(1)?.matrixEffect, 'despawn'); // still dematerializing
});

test('reduced: no autonomous motion, one held frame, decorative timers held', () => {
  const os = new OfficeState(floorLayout());
  os.reducedMotion = true;
  os.addAgent(1);
  os.setAgentActive(1, false); // idle: would wander without reduced motion
  const ch = os.characters.get(1)!;
  const at = { x: ch.x, y: ch.y };
  for (let i = 0; i < 1200; i++) os.update(0.25); // 5 simulated minutes
  assert.deepEqual({ x: ch.x, y: ch.y }, at);
  assert.equal(ch.state, CharacterState.IDLE);
  assert.equal(ch.frame, 0);
  assert.equal(os.furnitureAnimTimer, 0);
});

test('reduced: activity is READ, never written — active types, idle stands, flags untouched', () => {
  const seat: Seat = {
    uid: 'chair',
    seatCol: 3,
    seatRow: 2,
    facingDir: Direction.UP,
    assigned: true,
  };
  const seats = new Map([[seat.uid, seat]]);
  const ch = createCharacter(7, 0, seat.uid, seat, 0);
  ch.tileCol = 0;
  ch.tileRow = 0;
  ch.isActive = true;
  settleCharacter(ch, seats);
  assert.deepEqual(
    [ch.tileCol, ch.tileRow, ch.dir, ch.state],
    [3, 2, Direction.UP, CharacterState.TYPE],
  );
  assert.equal(ch.isActive, true);
  ch.isActive = false;
  settleCharacter(ch, seats);
  assert.equal(ch.state, CharacterState.IDLE);
  assert.equal(ch.isActive, false);
});

test('OS preference: unavailable or throwing matchMedia is NO preference; changes are followed live', () => {
  const seen: boolean[] = [];
  subscribeReducedMotion((r) => seen.push(r), undefined);
  subscribeReducedMotion(
    (r) => seen.push(r),
    () => {
      throw new Error('no matchMedia');
    },
  );
  assert.deepEqual(seen, [false, false]);

  let listener: ((e: { matches: boolean }) => void) | null = null;
  const query: PreferenceQuery = {
    matches: true,
    addEventListener: (_, l) => {
      listener = l;
    },
    removeEventListener: () => {
      listener = null;
    },
  };
  const live: boolean[] = [];
  const unsubscribe = subscribeReducedMotion(
    (r) => live.push(r),
    () => query,
  );
  listener!({ matches: false });
  unsubscribe();
  assert.deepEqual(live, [true, false]);
  assert.equal(listener, null);
});

test('PREFERENCE TRANSITION: enabling reduced motion COMPLETES effects already in flight in the next tick', () => {
  const os = new OfficeState(floorLayout());
  os.addAgent(2);
  os.update(MATRIX_EFFECT_DURATION + 0.05); // 2 fully spawned (flag off)
  os.addAgent(1); // 1 mid-spawn
  os.removeAgent(2); // 2 mid-despawn
  os.update(FRAME);
  assert.equal(os.characters.get(1)?.matrixEffect, 'spawn');
  assert.equal(os.characters.get(2)?.matrixEffect, 'despawn');
  os.reducedMotion = true; // the OS preference flips mid-session
  os.update(FRAME);
  assert.equal(os.characters.get(1)?.matrixEffect, null); // reveal finished, not stranded
  assert.equal(os.characters.has(2), false); // departure finished: removed, not left drawn
});
