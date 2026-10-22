/**
 * A 2D CONTEXT LOST AND RESTORED comes back EMPTY (render defect: textured floors and furniture disappear while the flat base
 * colours, which are painted every frame, stay).
 *
 * The office keeps hundreds of canvases that are drawn ONCE and blitted every frame: a canvas per sprite per zoom (spriteCache.ts),
 * the pre-composed carpet layer (renderer.ts) and the city's two light sprites (city/cityRender.ts). When the browser loses the
 * canvas contexts and restores them (the trigger is not determined here), every one of those bitmaps is blank, but nothing redrew them and nothing
 * forgot them, so the world stayed blank for good. The supported condition is exactly this: a `contextlost` or `contextrestored`
 * event delivered to the OFFICE canvas (the one startGameLoop owns) or to the CARPET LAYER canvas invalidates those three caches
 * through the render-cache lifecycle (renderCaches.ts resetRenderCaches), and the next draw rebuilds them lazily.
 *
 * The tests MODEL the blank state (every canvas made so far has its pixels dropped, as a restore leaves them) and show that a
 * draw after the event uses a REBUILT, painted bitmap. A before/after on healthy caches would prove only that nothing broke.
 */
import assert from 'node:assert/strict';

import { afterAll, beforeAll, beforeEach, test } from 'vitest';

import { CITY_LIGHT_SPRITE_PX } from '../src/constants.ts';
import { buildCityMask } from '../src/office/city/cityMask.ts';
import { renderCityLighting, setCityLayerFactory } from '../src/office/city/cityRender.ts';
import { startGameLoop } from '../src/office/engine/gameLoop.ts';
import { carpetLayerBuilds, renderCarpetLayer } from '../src/office/engine/renderer.ts';
import { renderCacheStats, resetRenderCaches } from '../src/office/renderCaches.ts';
import { setCarpetSprites } from '../src/office/sprites/carpetTiles.ts';
import { getCachedSprite } from '../src/office/sprites/spriteCache.ts';
import type { CarpetTile, SpriteData } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

/** A canvas whose bitmap can be dropped, and that resets when it is resized (as a real canvas does). */
class FakeCtx {
  imageSmoothingEnabled = true;
  fillStyle: unknown = '';
  globalAlpha = 1;
  globalCompositeOperation = 'source-over';
  drawn: unknown[] = [];
  private readonly owner: FakeCanvas;
  constructor(owner: FakeCanvas) {
    this.owner = owner;
  }
  fillRect(): void {
    this.owner.painted++;
  }
  clearRect(): void {}
  save(): void {}
  restore(): void {}
  drawImage(source: unknown): void {
    this.drawn.push(source);
    if ((source as FakeCanvas).painted > 0) this.owner.painted++;
  }
  createRadialGradient(): { addColorStop(): void } {
    return { addColorStop: () => undefined };
  }
}
class FakeCanvas extends EventTarget {
  /** How many painting operations reached this bitmap since it was last cleared. 0 = a blank bitmap. */
  painted = 0;
  private w = 0;
  private h = 0;
  private context: FakeCtx | null = null;
  get width(): number {
    return this.w;
  }
  set width(v: number) {
    this.w = v;
    this.painted = 0;
  }
  get height(): number {
    return this.h;
  }
  set height(v: number) {
    this.h = v;
    this.painted = 0;
  }
  getContext(): FakeCtx {
    return (this.context ??= new FakeCtx(this));
  }
  /** What a context restore leaves behind: the bitmap is EMPTY. */
  blank(): void {
    this.painted = 0;
  }
}

const g = globalThis as Record<string, unknown>;
const saved: Record<string, unknown> = {};
const made: FakeCanvas[] = [];
const HOOKS = ['document', 'requestAnimationFrame', 'cancelAnimationFrame'] as const;

beforeAll(() => {
  for (const k of HOOKS) saved[k] = g[k];
  g['document'] = {
    visibilityState: 'visible',
    createElement: (): FakeCanvas => {
      const c = new FakeCanvas();
      made.push(c);
      return c;
    },
  };
  g['requestAnimationFrame'] = () => 1;
  g['cancelAnimationFrame'] = () => undefined;
});
afterAll(() => {
  for (const k of HOOKS) {
    if (saved[k] === undefined) delete g[k];
    else g[k] = saved[k];
  }
  setCityLayerFactory(null);
});
beforeEach(() => {
  made.length = 0;
  setCityLayerFactory(null);
  resetRenderCaches();
  SPRITE = makeSprite(); // a new sprite object per test: its cached canvases are keyed by it
});

/* eslint-disable pixel-agents/no-inline-colors -- synthetic sprite pixels as test data */
const makeSprite = (): SpriteData => Array.from({ length: 4 }, () => Array(4).fill('#808080'));
let SPRITE: SpriteData = makeSprite();
const sheet = (): SpriteData[][] =>
  Array.from({ length: 2 }, () =>
    Array.from({ length: 16 }, () =>
      Array.from({ length: 16 }, (_, y) =>
        Array.from({ length: 16 }, (_, x) => ((x + y) % 2 ? '#c0c0c0' : '#404040')),
      ),
    ),
  );
/* eslint-enable pixel-agents/no-inline-colors */

/** The browser lost the contexts and restored them: every bitmap made so far is empty. */
const restoredEmpty = (): void => {
  for (const c of made) c.blank();
};

/** Run `body` with the office's game loop started on a main canvas, as OfficeCanvas starts it. */
function withLoop(body: (main: FakeCanvas) => void): void {
  const main = new FakeCanvas();
  const stop = startGameLoop(main as unknown as HTMLCanvasElement, {
    update: () => undefined,
    render: () => undefined,
  });
  try {
    body(main);
  } finally {
    stop();
  }
}

const sprite = (zoom: number): FakeCanvas => getCachedSprite(SPRITE, zoom) as unknown as FakeCanvas;

for (const kind of ['contextrestored', 'contextlost'] as const) {
  test(`a cached sprite bitmap that came back BLANK is rebuilt painted after the office canvas fires ${kind}`, () => {
    withLoop((main) => {
      const before = sprite(2);
      assert.ok(before.painted > 0, 'a healthy bitmap to begin with');
      assert.equal(sprite(2), before, 'cached: the same canvas on the next draw');
      restoredEmpty();
      assert.equal(sprite(2), before, 'with no event the blank bitmap is still served (the defect)');
      assert.equal(sprite(2).painted, 0);
      const resets = renderCacheStats().resets;
      main.dispatchEvent(new Event(kind));
      const after = sprite(2);
      assert.notEqual(after, before, 'a NEW canvas is made after the event');
      assert.ok(after.painted > 0, 'and it is painted, not blank');
      assert.equal(renderCacheStats().resets, resets + 1, 'through the existing render-cache lifecycle');
      assert.equal(sprite(2), after, 'and it is cached again (rebuilt lazily, once)');
    });
  });
}

test('every zoom of a sprite is rebuilt, not only the zoom drawn last', () => {
  withLoop((main) => {
    const zooms = [1, 2, 4];
    const before = zooms.map(sprite);
    restoredEmpty();
    main.dispatchEvent(new Event('contextrestored'));
    zooms.forEach((z, i) => {
      const after = sprite(z);
      assert.notEqual(after, before[i], `zoom ${z}`);
      assert.ok(after.painted > 0, `zoom ${z} is painted`);
    });
  });
});

const CARPETS: Array<CarpetTile | null> = [{ variant: 0 }, { variant: 1 }, null, { variant: 0 }];
/** Draw the carpet layer to a fresh sink and return the canvas the frame blitted. */
function blitCarpetLayer(): FakeCanvas {
  const sink = new FakeCanvas();
  renderCarpetLayer(sink.getContext() as unknown as CanvasRenderingContext2D, CARPETS, 2, 2, 0, 0, 2);
  const blitted = sink.getContext().drawn.at(-1);
  assert.ok(blitted, 'the frame blitted the carpet layer');
  return blitted as FakeCanvas;
}

test('the pre-composed carpet layer, blank after a restore, is rebuilt into a FRESH canvas element (office canvas event)', () => {
  setCarpetSprites(sheet());
  resetRenderCaches();
  withLoop((main) => {
    const layer = blitCarpetLayer();
    assert.ok(layer.painted > 0, 'a healthy layer to begin with');
    const builds = carpetLayerBuilds();
    restoredEmpty();
    assert.equal(blitCarpetLayer(), layer, 'with no event the blank layer is still blitted (the defect)');
    assert.equal(carpetLayerBuilds(), builds);
    main.dispatchEvent(new Event('contextrestored'));
    const after = blitCarpetLayer();
    assert.notEqual(after, layer, 'a FRESH element: the old one may still own a lost context');
    assert.ok(after.painted > 0, 'painted, not blank');
    assert.equal(carpetLayerBuilds(), builds + 1, 'rebuilt once');
  });
});

test('the carpet layer canvas reporting its OWN loss and restore is enough, whichever event arrives first', () => {
  setCarpetSprites(sheet());
  resetRenderCaches();
  withLoop(() => {
    const layer = blitCarpetLayer();
    restoredEmpty();
    layer.dispatchEvent(new Event('contextrestored')); // the main canvas has said nothing
    const after = blitCarpetLayer();
    assert.notEqual(after, layer);
    assert.ok(after.painted > 0);
  });
});

test('the city light sprites, drawn once and kept, are rebuilt painted after the restore', () => {
  const frame = { canvasWidth: 800, canvasHeight: 600, offsetX: 0, offsetY: 0, zoom: 2, view: { c0: 0, c1: 26, r0: 0, r1: 20 } };
  const input = {
    level: 1,
    mask: buildCityMask(40, 40, new Array(1600).fill(TileType.FLOOR_15)),
    lamps: [{ x: 40, y: 60 }],
    agents: [{ x: 80, y: 60 }],
  };
  const main = new FakeCanvas();
  const lightSprites = (): FakeCanvas[] =>
    made.filter((c) => c.width === CITY_LIGHT_SPRITE_PX && c.height === CITY_LIGHT_SPRITE_PX);
  withLoop(() => {
    renderCityLighting(main.getContext() as never, input, frame);
    const before = lightSprites();
    assert.ok(before.length >= 2 && before.every((c) => c.painted > 0), 'two painted light sprites to begin with');
    restoredEmpty();
    renderCityLighting(main.getContext() as never, input, frame);
    assert.equal(lightSprites().length, before.length, 'with no event no sprite is rebuilt (the defect)');
  });
  withLoop((office) => {
    office.dispatchEvent(new Event('contextrestored'));
    renderCityLighting(main.getContext() as never, input, frame);
    const rebuilt = lightSprites().filter((c) => c.painted > 0);
    assert.ok(rebuilt.length >= 2, 'painted light sprites exist after the event');
  });
});

test('once the loop is stopped it stops listening: no reset from a canvas it no longer owns', () => {
  const main = new FakeCanvas();
  const stop = startGameLoop(main as unknown as HTMLCanvasElement, { update: () => undefined, render: () => undefined });
  stop();
  const resets = renderCacheStats().resets;
  main.dispatchEvent(new Event('contextrestored'));
  main.dispatchEvent(new Event('contextlost'));
  assert.equal(renderCacheStats().resets, resets);
});
