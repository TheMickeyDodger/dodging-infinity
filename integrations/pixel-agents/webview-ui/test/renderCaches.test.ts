/**
 * THE RENDER-CACHE LIFECYCLE (office/renderCaches.ts): the strong caches of derived sprites are
 * cleared on a reset (every layout load / replacement) and bounded across rebuilds; the carpet
 * junction plan is part of the same lifecycle; and the pre-composed carpet layer of ANY admitted
 * grid fits its pixel budget, so an admitted layout never falls back to per-junction drawing.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import { LAYOUT_MAX_CELLS, LAYOUT_MAX_DIM } from '../../core/src/layoutLimits.ts';
import { CARPET_LAYER_MAX_PIXELS, RENDER_CACHE_MAX_ENTRIES, TILE_SIZE } from '../src/constants.ts';
import { colorizeCacheSize, getColorizedSprite } from '../src/office/colorize.ts';
import { carpetJunctionPlan } from '../src/office/engine/carpetPlan.ts';
import {
  boundRenderCaches,
  renderCacheStats,
  resetRenderCaches,
} from '../src/office/renderCaches.ts';
import { setCarpetSprites } from '../src/office/sprites/carpetTiles.ts';
import type { CarpetTile, SpriteData } from '../src/office/types.ts';

/* eslint-disable pixel-agents/no-inline-colors -- synthetic sprite pixels as test data */
const SPRITE: SpriteData = Array.from({ length: 4 }, () => Array(4).fill('#808080'));
const sheet = (): SpriteData[][] =>
  Array.from({ length: 2 }, () =>
    Array.from({ length: 16 }, () =>
      Array.from({ length: 16 }, (_, y) =>
        Array.from({ length: 16 }, (_, x) => ((x + y) % 2 ? '#c0c0c0' : '#404040')),
      ),
    ),
  );
/* eslint-enable pixel-agents/no-inline-colors */
const fill = (n: number, from = 0) => {
  for (let i = from; i < from + n; i++)
    getColorizedSprite(`t-${i}`, SPRITE, { h: i % 360, s: 0, b: 0, c: 0, colorize: true });
};

test('a reset clears the colorize and carpet caches and drops the carpet plan', () => {
  setCarpetSprites(sheet());
  resetRenderCaches();
  fill(50);
  const carpets: Array<CarpetTile | null> = [{ variant: 0 }, { variant: 1 }, null, { variant: 0 }];
  const plan = carpetJunctionPlan(carpets, 2, 2);
  assert.ok(plan.sprites.length > 0);
  const filled = renderCacheStats();
  assert.ok(filled.colorizeEntries >= 50 && filled.carpetEntries > 0, JSON.stringify(filled));
  resetRenderCaches();
  const cleared = renderCacheStats();
  assert.equal(cleared.colorizeEntries, 0);
  assert.equal(cleared.carpetEntries, 0);
  assert.notEqual(carpetJunctionPlan(carpets, 2, 2), plan, 'the plan is rebuilt after a reset');
});

test('the bound: no clear at or under RENDER_CACHE_MAX_ENTRIES, a clear once over it', () => {
  resetRenderCaches();
  fill(RENDER_CACHE_MAX_ENTRIES);
  assert.equal(boundRenderCaches(), false, 'at the bound: kept warm');
  assert.equal(colorizeCacheSize(), RENDER_CACHE_MAX_ENTRIES);
  fill(1, RENDER_CACHE_MAX_ENTRIES);
  assert.equal(boundRenderCaches(), true, 'over the bound: cleared');
  assert.equal(colorizeCacheSize(), 0);
});

test('REPEATED cycles with DIFFERENT palettes stay bounded (the lifecycle, not one import)', () => {
  resetRenderCaches();
  const sizes: number[] = [];
  for (let cycle = 0; cycle < 12; cycle++) {
    resetRenderCaches(); // a layout load / replacement
    fill(2_000, cycle * 2_000); // its palettes: all NEW keys each cycle
    for (let e = 0; e < 5; e++) {
      boundRenderCaches(); // an edit's rebuild
      fill(500, 1_000_000 + cycle * 10_000 + e * 500); // each edit brings new palettes too
    }
    sizes.push(colorizeCacheSize());
  }
  assert.ok(
    sizes.every((n) => n <= RENDER_CACHE_MAX_ENTRIES + 500),
    `bounded every cycle: ${sizes.join(', ')}`,
  );
  assert.equal(
    new Set(sizes).size,
    1,
    `the same size every cycle, not growing: ${sizes.join(', ')}`,
  );
});

test('the carpet layer of ANY admitted grid fits CARPET_LAYER_MAX_PIXELS (never falls back)', () => {
  // the layer spans at most (cols + 1) x (rows + 1) junctions of TILE_SIZE x TILE_SIZE pixels
  let worst = 0;
  for (let cols = 1; cols <= LAYOUT_MAX_DIM; cols++) {
    const rows = Math.min(LAYOUT_MAX_DIM, Math.floor(LAYOUT_MAX_CELLS / cols));
    worst = Math.max(worst, (cols + 1) * (rows + 1) * TILE_SIZE * TILE_SIZE);
  }
  assert.ok(worst <= CARPET_LAYER_MAX_PIXELS, `worst admitted layer ${worst} px`);
});
