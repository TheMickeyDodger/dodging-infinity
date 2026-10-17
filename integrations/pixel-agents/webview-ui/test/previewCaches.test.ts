/**
 * EDITOR PREVIEW CACHES (review round 1, F1): a brush-colour change — floor, wall and carpet
 * swatches, catalogue thumbnails, the placement ghost — creates colorized sprites WITHOUT a layout
 * rebuild, so the rebuild-time bound (renderCaches.boundRenderCaches) cannot reach them. They go to
 * dedicated PREVIEW caches bounded AT INSERTION. Proven here over many rounds of new brush colours:
 * each preview cache stays within its cap, the LAYOUT caches gain nothing, and a render-cache reset
 * clears the previews too.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import { CARPET_PREVIEW_CACHE_MAX_ENTRIES, PREVIEW_CACHE_MAX_ENTRIES } from '../src/constants.ts';
import {
  colorizeCacheSize,
  getPreviewColorizedSprite,
  previewCacheSize,
} from '../src/office/colorize.ts';
import { getPreviewFloorSprite, setFloorSprites } from '../src/office/floorTiles.ts';
import { renderCacheStats, resetRenderCaches } from '../src/office/renderCaches.ts';
import {
  carpetCacheSize,
  carpetPreviewCacheSize,
  getCarpetPreviewJunctionSprite,
  setCarpetSprites,
} from '../src/office/sprites/carpetTiles.ts';
import type { CarpetTile, SpriteData } from '../src/office/types.ts';

/* eslint-disable pixel-agents/no-inline-colors -- synthetic sprite pixels as test data */
const grey = (n: number): SpriteData =>
  Array.from({ length: n }, (_, y) =>
    Array.from({ length: n }, (_, x) => ((x + y) % 2 ? '#c0c0c0' : '#404040')),
  );
/* eslint-enable pixel-agents/no-inline-colors */
const brush = (k: number) => ({
  h: (k % 361) - 180,
  s: (Math.floor(k / 361) % 201) - 100,
  b: 0,
  c: 0,
  colorize: true,
});

test('floor brush: every round of new colours stays within the preview cap; the layout cache gains nothing', () => {
  resetRenderCaches();
  setFloorSprites(Array.from({ length: 9 }, () => grey(16)));
  const layoutBefore = colorizeCacheSize();
  const sizes: number[] = [];
  for (let round = 0; round < 40; round++) {
    // one brush colour per round, every floor swatch redrawn with it (as the toolbar does)
    for (let pattern = 0; pattern < 9; pattern++)
      getPreviewFloorSprite(pattern, brush(round * 997));
    // and the brush dragged through 100 more colours
    for (let k = 0; k < 100; k++) getPreviewFloorSprite(round % 9, brush(round * 997 + k + 1));
    sizes.push(previewCacheSize());
  }
  assert.ok(
    sizes.every((n) => n <= PREVIEW_CACHE_MAX_ENTRIES),
    `bounded: max ${Math.max(...sizes)}`,
  );
  assert.ok(sizes.length * 109 > PREVIEW_CACHE_MAX_ENTRIES, 'the rounds made more than the cap');
  assert.equal(colorizeCacheSize(), layoutBefore, 'previews never enter the layout cache');
});

test('furniture thumbnails, wall swatches and the ghost: bounded at insertion, layout cache untouched', () => {
  resetRenderCaches();
  const sprite = grey(16);
  const layoutBefore = colorizeCacheSize();
  let max = 0;
  for (let round = 0; round < 30; round++) {
    // a catalogue page of 330 thumbnails for this round's brush colour, plus the ghost and walls
    for (let t = 0; t < 330; t++)
      getPreviewColorizedSprite(`thumb-T${t}-${round}`, sprite, brush(round));
    getPreviewColorizedSprite(`ghost-T0-${round}`, sprite, brush(round));
    for (let w = 0; w < 4; w++)
      getPreviewColorizedSprite(`wall-preview-${w}-${round}`, sprite, brush(round));
    max = Math.max(max, previewCacheSize());
  }
  assert.ok(max <= PREVIEW_CACHE_MAX_ENTRIES, `bounded: max ${max}`);
  assert.equal(colorizeCacheSize(), layoutBefore);
});

test('carpet brush: bounded at insertion in its own preview cache; the layout carpet cache gains nothing', () => {
  resetRenderCaches();
  setCarpetSprites(Array.from({ length: 3 }, () => Array.from({ length: 16 }, () => grey(16))));
  const layoutBefore = carpetCacheSize();
  let max = 0;
  for (let round = 0; round < 200; round++) {
    const color = brush(round * 7);
    const accent = brush(round * 7 + 3);
    for (let variant = 0; variant < 3; variant++) {
      const tile: CarpetTile = { variant, color, accentColor: accent };
      const strip: Array<CarpetTile | null> = [tile, tile];
      for (let jy = 0; jy <= 1; jy++)
        for (let jx = 0; jx <= 2; jx++)
          getCarpetPreviewJunctionSprite(jx, jy, variant, strip, 2, 1, color, accent);
    }
    max = Math.max(max, carpetPreviewCacheSize());
  }
  assert.ok(max <= CARPET_PREVIEW_CACHE_MAX_ENTRIES, `bounded: max ${max}`);
  assert.ok(max > 0, 'the swatches were drawn');
  assert.equal(carpetCacheSize(), layoutBefore, 'previews never enter the layout carpet cache');
});

test('a render-cache reset clears the preview caches too, and they are accounted for', () => {
  getPreviewColorizedSprite('x', grey(4), brush(1));
  assert.ok(renderCacheStats().previewEntries > 0);
  resetRenderCaches();
  const s = renderCacheStats();
  assert.equal(s.previewEntries, 0);
  assert.equal(s.carpetPreviewEntries, 0);
});
