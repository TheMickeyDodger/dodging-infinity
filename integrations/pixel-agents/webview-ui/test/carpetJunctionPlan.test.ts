/**
 * THE CARPET JUNCTION PLAN (engine/carpetPlan.ts, drawn by renderer.ts): the per-junction carpet resolution moved out of the
 * frame into a plan built once per invalidation. Proven here:
 *  - EQUIVALENCE: for seeded random carpets (mixed variants, colours, orders, gaps), every
 *    junction's planned layers are EXACTLY what the previous per-frame algorithm resolved (kept
 *    below verbatim as the oracle), in the same order;
 *  - REUSE: the same carpets, shape and sprite generation return the same plan (no rebuild);
 *  - INVALIDATION, each listed cause: an editor carpet paint and erase (editorActions copy the
 *    array), a grid expansion (new shape), and a carpet sprite set replaced (setCarpetSprites
 *    bumps the presentation generation) — and the rebuilt plan shows the edit.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import type { ColorValue } from '../src/components/ui/types.ts';
import { CARPET_DEFAULT_ACCENT_COLOR, CARPET_DEFAULT_COLOR } from '../src/constants.ts';
import { eraseCarpet, expandLayout, paintCarpet } from '../src/office/editor/editorActions.ts';
import { carpetJunctionPlan } from '../src/office/engine/carpetPlan.ts';
import {
  getCarpetJunctionSprite,
  getCarpetPaletteKey,
  setCarpetSprites,
} from '../src/office/sprites/carpetTiles.ts';
import type { CarpetTile, OfficeLayout, SpriteData } from '../src/office/types.ts';

/* eslint-disable pixel-agents/no-inline-colors -- synthetic sprite pixels and colours as test data */
/** 16 marching-squares cases per variant, two grey levels each (so a main AND an accent). */
const sheet = (seed: number): SpriteData[][] =>
  Array.from({ length: 3 }, (_, v) =>
    Array.from({ length: 16 }, (_, ms) =>
      Array.from({ length: 16 }, (_, y) =>
        Array.from({ length: 16 }, (_, x) =>
          (x + y + ms + v + seed) % 3 === 0 ? '#c0c0c0' : '#404040',
        ),
      ),
    ),
  );
const PALETTE: ColorValue[] = [
  { h: 10, s: 40, b: 0, c: 0, colorize: true },
  { h: 200, s: 20, b: -10, c: 0, colorize: true },
  { h: 90, s: 60, b: 5, c: 5 },
];
/* eslint-enable pixel-agents/no-inline-colors */

/** Seeded PRNG (mulberry32), so a failure reproduces. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randomCarpets(cols: number, rows: number, seed: number): Array<CarpetTile | null> {
  const r = rng(seed);
  return Array.from({ length: cols * rows }, () => {
    if (r() < 0.35) return null;
    const tile: CarpetTile = { variant: Math.floor(r() * 3), order: Math.floor(r() * 7) - 3 };
    if (r() < 0.7) tile.color = PALETTE[Math.floor(r() * PALETTE.length)];
    if (r() < 0.5) tile.accentColor = PALETTE[Math.floor(r() * PALETTE.length)];
    return tile;
  });
}

/** THE ORACLE: the per-frame resolution as it was before the plan (renderCarpetLayer body). */
function oracle(
  carpetTiles: Array<CarpetTile | null>,
  cols: number,
  rows: number,
  jx: number,
  jy: number,
): SpriteData[] {
  const localGroups = new Map<
    string,
    {
      variant: number;
      color: ColorValue;
      accentColor: ColorValue;
      paletteKey: string;
      order: number;
    }
  >();
  const adjacent = [
    { col: jx - 1, row: jy - 1 },
    { col: jx, row: jy - 1 },
    { col: jx, row: jy },
    { col: jx - 1, row: jy },
  ];
  for (const pos of adjacent) {
    if (pos.col < 0 || pos.row < 0 || pos.col >= cols || pos.row >= rows) continue;
    const tile = carpetTiles[pos.row * cols + pos.col];
    if (!tile) continue;
    const color = tile.color ?? CARPET_DEFAULT_COLOR;
    const accentColor = tile.accentColor ?? CARPET_DEFAULT_ACCENT_COLOR;
    const paletteKey = getCarpetPaletteKey(color, accentColor);
    const key = `${tile.variant}:${paletteKey}`;
    const order = tile.order ?? 0;
    const existing = localGroups.get(key);
    if (!existing || order > existing.order) {
      localGroups.set(key, { variant: tile.variant, color, accentColor, paletteKey, order });
    }
  }
  const out: SpriteData[] = [];
  const ordered = [...localGroups.values()].sort((a, b) => a.order - b.order);
  for (const { variant, color, accentColor, paletteKey } of ordered) {
    const sprite = getCarpetJunctionSprite(
      jx,
      jy,
      variant,
      carpetTiles,
      cols,
      rows,
      color,
      accentColor,
      paletteKey,
    );
    if (sprite) out.push(sprite);
  }
  return out;
}

const layers = (
  plan: ReturnType<typeof carpetJunctionPlan>,
  jx: number,
  jy: number,
): SpriteData[] => {
  const j = jy * (plan.cols + 1) + jx;
  return plan.sprites.slice(plan.offsets[j], plan.offsets[j + 1]);
};

test('EQUIVALENCE: every junction plans exactly the layers the per-frame algorithm drew', () => {
  setCarpetSprites(sheet(0));
  let compared = 0;
  let drawn = 0;
  for (const seed of [1, 2, 3, 4, 5]) {
    const cols = 11 + seed;
    const rows = 7 + seed;
    const carpets = randomCarpets(cols, rows, seed);
    const plan = carpetJunctionPlan(carpets, cols, rows);
    for (let jy = 0; jy <= rows; jy++)
      for (let jx = 0; jx <= cols; jx++) {
        const want = oracle(carpets, cols, rows, jx, jy);
        const got = layers(plan, jx, jy);
        assert.equal(got.length, want.length, `seed ${seed} junction ${jx},${jy}: layer count`);
        want.forEach((s, k) =>
          assert.ok(
            got[k] === s,
            `seed ${seed} junction ${jx},${jy}: layer ${k} is the same sprite`,
          ),
        );
        compared++;
        drawn += want.length;
      }
  }
  assert.ok(
    drawn > 500,
    `the comparison exercised real layers (${drawn} over ${compared} junctions)`,
  );
});

test('REUSE: the same carpets, shape and sprite generation return the same plan', () => {
  setCarpetSprites(sheet(0));
  const carpets = randomCarpets(10, 8, 9);
  const a = carpetJunctionPlan(carpets, 10, 8);
  assert.equal(carpetJunctionPlan(carpets, 10, 8), a);
});

test('INVALIDATION: editor paint and erase, grid expansion and a sprite reload each rebuild it', () => {
  setCarpetSprites(sheet(0));
  const cols = 8;
  const rows = 6;
  let layout: OfficeLayout = {
    version: 1,
    cols,
    rows,
    tiles: new Array(cols * rows).fill(1),
    furniture: [],
    carpetTiles: new Array(cols * rows).fill(null),
  };
  const empty = carpetJunctionPlan(layout.carpetTiles!, cols, rows);
  assert.equal(empty.sprites.length, 0, 'no carpets, nothing planned');

  // PAINT: the four corners of (3, 2) now draw it
  layout = paintCarpet(layout, 3, 2, 1, PALETTE[0]);
  const painted = carpetJunctionPlan(layout.carpetTiles!, cols, rows);
  assert.notEqual(painted, empty, 'a paint rebuilds the plan');
  for (const [jx, jy] of [
    [3, 2],
    [4, 2],
    [4, 3],
    [3, 3],
  ])
    assert.equal(layers(painted, jx, jy).length, 1, `corner ${jx},${jy} draws the painted carpet`);
  assert.equal(layers(painted, 0, 0).length, 0, 'an untouched corner draws nothing');

  // ERASE: gone again
  layout = eraseCarpet(layout, 3, 2);
  const erased = carpetJunctionPlan(layout.carpetTiles!, cols, rows);
  assert.notEqual(erased, painted, 'an erase rebuilds the plan');
  assert.equal(erased.sprites.length, 0, 'the erased carpet is no longer drawn');

  // EXPANSION: a new shape (and the carpet shifted with the grid)
  layout = paintCarpet(layout, 0, 0, 2);
  const before = carpetJunctionPlan(layout.carpetTiles!, cols, rows);
  const grown = expandLayout(layout, 'left')!;
  const after = carpetJunctionPlan(grown.layout.carpetTiles!, grown.layout.cols, grown.layout.rows);
  assert.notEqual(after, before, 'an expansion rebuilds the plan');
  assert.equal(layers(after, 0, 0).length, 0, 'the carpet moved right with the expansion');
  assert.equal(layers(after, 1, 0).length, 1, 'its NW corner is now junction 1,0');

  // SPRITE RELOAD: same carpets, new art -> rebuilt, drawing the NEW sprites
  const carpets = grown.layout.carpetTiles!;
  const old = carpetJunctionPlan(carpets, grown.layout.cols, grown.layout.rows);
  setCarpetSprites(sheet(1));
  const reloaded = carpetJunctionPlan(carpets, grown.layout.cols, grown.layout.rows);
  assert.notEqual(reloaded, old, 'a carpet sprite reload rebuilds the plan');
  assert.notEqual(layers(reloaded, 1, 0)[0], layers(old, 1, 0)[0], 'and draws the new art');
});
