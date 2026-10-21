/**
 * LIVING CITY 6–9, lighting: the real engine modules, judged by their own code. The level math, the tile
 * mask and the lamps on the SHIPPED living metropolis, and the render pass against a recording canvas that
 * emulates save/restore, so the compositing order, the per-frame caps and the absence of leaked state are
 * asserted on what is actually drawn.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { cityRgba } from '../src/constants.ts';
import * as L from '../src/office/city/cityLight.ts';
import * as M from '../src/office/city/cityMask.ts';
import * as R from '../src/office/city/cityRender.ts';
import { bumpPresentationGeneration } from '../src/office/presentationGeneration.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');

// ── level math ────────────────────────────────────────────────────────────────

test('the local clock shows night, a dawn ramp, day and a dusk ramp', () => {
  const table: Array<[number, number]> = [
    [0, 1],
    [3, 1],
    [4.99, 1],
    [5, 1],
    [5.5, 0.75],
    [6, 0.5],
    [6.5, 0.25],
    [7, 0],
    [12, 0],
    [16.99, 0],
    [17, 0],
    [18, 0.25],
    [19, 0.5],
    [20, 0.75],
    [21, 1],
    [23.5, 1],
  ];
  for (const [hour, level] of table)
    assert.ok(
      Math.abs(L.clockLevel(hour) - level) < 0.01,
      `${hour}h -> ${L.clockLevel(hour)}, want ${level}`,
    );
  // hours wrap: 25h is 1am, -1h is 11pm
  assert.equal(L.clockLevel(25), 1);
  assert.equal(L.clockLevel(-1), 1);
});

test('the nearest still stage is the one a level rounds to', () => {
  assert.equal(L.nearestStage(0), 'day');
  assert.equal(L.nearestStage(0.24), 'day');
  assert.equal(L.nearestStage(0.25), 'dusk');
  assert.equal(L.nearestStage(0.74), 'dusk');
  assert.equal(L.nearestStage(0.75), 'night');
  assert.equal(L.nearestStage(1), 'night');
  assert.deepEqual((['day', 'dusk', 'night'] as const).map(L.stageLevel), [0, 0.5, 1]);
});

test('manual modes are still stages whatever the clock says; the clock mode follows local time', () => {
  const evening = new Date(2026, 9, 20, 19, 30, 0).getTime(); // local 19:30 -> level 0.625
  const noon = new Date(2026, 9, 20, 12, 0, 0).getTime();
  for (const now of [evening, noon]) {
    assert.equal(L.targetLevel('day', now, false), 0);
    assert.equal(L.targetLevel('dusk', now, false), 0.5);
    assert.equal(L.targetLevel('night', now, false), 1);
  }
  assert.ok(Math.abs(L.localHours(evening) - 19.5) < 1e-9);
  assert.ok(Math.abs(L.targetLevel('clock', evening, false) - 0.625) < 1e-9);
  assert.equal(L.targetLevel('clock', noon, false), 0);
});

test('under reduced motion the clock shows the nearest still stage, never a ramp', () => {
  const at = (h: number, m = 0) => new Date(2026, 9, 20, h, m, 0).getTime();
  assert.equal(L.targetLevel('clock', at(17, 30), true), 0); // 0.125 -> day
  assert.equal(L.targetLevel('clock', at(18, 30), true), 0.5); // 0.375 -> dusk
  assert.equal(L.targetLevel('clock', at(19, 30), true), 0.5); // 0.625 -> dusk
  assert.equal(L.targetLevel('clock', at(20, 30), true), 1); // 0.875 -> night
});

test('the shown level fades over CITY_LIGHT_FADE_SEC, never overshoots, and jumps under reduced motion', () => {
  assert.equal(L.approachLevel(0, 1, 0.4, false), 0.5);
  assert.equal(L.approachLevel(0.5, 1, 0.4, false), 1);
  assert.equal(L.approachLevel(0.9, 1, 5, false), 1); // no overshoot
  assert.equal(L.approachLevel(1, 0, 0.4, false), 0.5);
  assert.equal(L.approachLevel(0.3, 1, 0, false), 0.3); // no time, no change
  assert.equal(L.approachLevel(0.3, 1, 0.01, true), 1); // reduced motion: at once
});

test('the darkness colour is the stage colour at the stages and interpolates between them', () => {
  assert.deepEqual(L.levelColor(0), { r: 0, g: 0, b: 0, a: 0 });
  assert.deepEqual(L.levelColor(0.5), { r: 74, g: 38, b: 66, a: 0.3 });
  assert.deepEqual(L.levelColor(1), { r: 8, g: 14, b: 46, a: 0.58 });
  assert.deepEqual(L.levelColor(0.25), { r: 37, g: 19, b: 33, a: 0.15 });
  assert.deepEqual(L.levelColor(-3), L.levelColor(0)); // clamped
  assert.deepEqual(L.levelColor(9), L.levelColor(1));
});

test('the status line says what is true of each mode, including that the clock is not a simulation', () => {
  const t = new Date(2026, 9, 20, 19, 20, 0).getTime();
  assert.match(L.lightingStatus('day', t), /as it always looked/);
  assert.match(L.lightingStatus('dusk', t), /still stage you chose/);
  assert.match(L.lightingStatus('night', t), /still stage you chose/);
  assert.match(L.lightingStatus('clock', t), /this computer's clock \(19:20\): dusk/);
});

// ── the tile mask and the lamps ───────────────────────────────────────────────

test('tiles are classed outside, outdoors or indoors by their type', () => {
  assert.equal(M.classifyTile(TileType.VOID), M.MASK_NONE);
  for (const t of [
    TileType.WALL,
    TileType.FLOOR_1,
    TileType.FLOOR_9,
    TileType.FLOOR_25,
    TileType.FLOOR_26,
  ])
    assert.equal(M.classifyTile(t), M.MASK_INDOOR, String(t));
  // streets, crossings, grass, forest, water, sand, plaza, soil, crops are outdoors
  for (const t of [
    TileType.FLOOR_10,
    TileType.FLOOR_14,
    TileType.FLOOR_15,
    TileType.FLOOR_16,
    TileType.FLOOR_17,
    TileType.FLOOR_18,
    TileType.FLOOR_19,
    TileType.FLOOR_20,
    TileType.FLOOR_22,
    TileType.FLOOR_24,
    TileType.FLOOR_27,
    TileType.FLOOR_28,
  ])
    assert.equal(M.classifyTile(t), M.MASK_OUTDOOR, String(t));
});

test("the outdoor tile above a wall is that wall's lit facade; nothing else is reclassified", () => {
  // 4 x 3. Row 2 has a wall at col 1. Above it (col 1, row 1) is street: it becomes indoor facade.
  const A = TileType.FLOOR_15;
  const tiles = [A, A, A, A, /**/ A, A, A, A, /**/ A, TileType.WALL, A, A];
  const mask = M.buildCityMask(4, 3, tiles);
  const at = (c: number, r: number) => M.maskAt(mask, c, r);
  assert.equal(at(1, 2), M.MASK_INDOOR);
  assert.equal(at(1, 1), M.MASK_INDOOR); // the facade
  assert.equal(at(1, 0), M.MASK_OUTDOOR); // two above: untouched
  assert.equal(at(0, 1), M.MASK_OUTDOOR);
  assert.equal(at(2, 1), M.MASK_OUTDOOR);
  // a wall in the top row has nothing above it and cannot read outside the grid
  const top = M.buildCityMask(2, 1, [TileType.WALL, A]);
  assert.equal(M.maskAt(top, 0, 0), M.MASK_INDOOR);
  // an indoor tile above a wall stays indoor
  const stack = M.buildCityMask(1, 2, [TileType.FLOOR_26, TileType.WALL]);
  assert.equal(M.maskAt(stack, 0, 0), M.MASK_INDOOR);
  // off the grid is nothing
  assert.equal(M.maskAt(mask, -1, 0), M.MASK_NONE);
  assert.equal(M.maskAt(mask, 4, 0), M.MASK_NONE);
  assert.equal(M.maskAt(mask, 0, 3), M.MASK_NONE);
});

test('indoor runs are the maximal runs inside the window', () => {
  const W = TileType.FLOOR_26;
  const A = TileType.FLOOR_15;
  const mask = M.buildCityMask(8, 2, [A, W, W, A, W, A, W, W, /**/ W, W, A, A, A, A, A, W]);
  const runs: Array<[number, number, number]> = [];
  M.indoorRuns(mask, 0, 8, 0, 2, (r, a, b) => runs.push([r, a, b]));
  assert.deepEqual(runs, [
    [0, 1, 3],
    [0, 4, 5],
    [0, 6, 8],
    [1, 0, 2],
    [1, 7, 8],
  ]);
  // a window clips a run, and a window past the grid is empty
  const clipped: Array<[number, number, number]> = [];
  M.indoorRuns(mask, 2, 7, 0, 1, (r, a, b) => clipped.push([r, a, b]));
  assert.deepEqual(clipped, [
    [0, 2, 3],
    [0, 4, 5],
    [0, 6, 7],
  ]);
  let any = 0;
  M.indoorRuns(mask, 20, 30, 20, 30, () => any++);
  assert.equal(any, 0);
});

test('a mask is built once per tile array and rebuilt when the layout is replaced', () => {
  const tiles = [TileType.FLOOR_15, TileType.WALL];
  const layout = { cols: 2, rows: 1, tiles };
  const first = M.cityMaskFor(layout);
  assert.equal(M.cityMaskFor(layout), first);
  const edited = M.cityMaskFor({ cols: 2, rows: 1, tiles: [...tiles] }); // an edit replaces the array
  assert.notEqual(edited, first);
  assert.equal(M.cityMaskFor({ cols: 2, rows: 1, tiles }), first); // the old one is still cached for its own array
});

let world: OfficeLayout;
beforeAll(() => {
  world = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
});

test("on the shipped metropolis: every road tile is outdoors except the few under a wall's raised face, and offices are indoors", () => {
  const mask = M.cityMaskFor(world);
  assert.equal(mask.cells.length, world.cols * world.rows);
  let road = 0;
  let roadIndoor = 0;
  let office = 0;
  let officeOutdoor = 0;
  for (let i = 0; i < world.tiles.length; i++) {
    const t = world.tiles[i];
    if (t === 15 || t === 16 || t === 17 || t === 27) {
      road++;
      if (mask.cells[i] === M.MASK_INDOOR) {
        roadIndoor++;
        // such a tile has a wall directly beneath it
        assert.equal(
          world.tiles[i + world.cols],
          TileType.WALL,
          `road tile ${i} is indoor without a wall beneath`,
        );
      }
    }
    if (t === TileType.FLOOR_26) {
      office++;
      if (mask.cells[i] !== M.MASK_INDOOR) officeOutdoor++;
    }
  }
  assert.ok(road > 4000, `expected the ~4250 road tiles, saw ${road}`);
  assert.ok(roadIndoor < road * 0.01, `${roadIndoor} of ${road} road tiles went indoor`);
  assert.ok(office > 1000);
  assert.equal(officeOutdoor, 0);
});

test('the lamps of the shipped metropolis are found by type and placed at their footprint, cached per layout', () => {
  const lamps = M.lampLights(world, () => ({ w: 1, h: 3 }));
  const wanted = world.furniture.filter((f) =>
    ['DETAIL_LAMP_ORNATE', 'DETAIL_LAMP_MODERN', 'DECK_LANTERN'].includes(f.type),
  );
  assert.equal(lamps.length, wanted.length);
  assert.equal(lamps.length, 110 + 32 + 6);
  assert.deepEqual(lamps[0], { x: (wanted[0].col + 0.5) * 16, y: (wanted[0].row + 0.5) * 16 });
  assert.equal(
    M.lampLights(world, () => ({ w: 1, h: 3 })),
    lamps,
  ); // same array, same generation: cached
  bumpPresentationGeneration(); // the footprints came from the catalogue, which just reloaded
  assert.notEqual(
    M.lampLights(world, () => ({ w: 1, h: 3 })),
    lamps,
  );
  // a footprint the catalogue does not know yet falls back to one tile
  const fallback = M.lampLights(
    { furniture: [{ type: 'DETAIL_LAMP_MODERN', col: 2, row: 4 }] },
    () => null,
  );
  assert.deepEqual(fallback, [{ x: 40, y: 72 }]);
});

// ── the render pass, against a recording canvas ───────────────────────────────

interface Call {
  op: string;
  args: unknown[];
  comp: string;
  alpha: number;
  fill: unknown;
}
class FakeCtx implements R.CityCtx {
  globalCompositeOperation = 'source-over';
  globalAlpha = 1;
  fillStyle: unknown = '';
  imageSmoothingEnabled = false;
  calls: Call[] = [];
  depth = 0;
  private stack: Array<[string, number, unknown, boolean]> = [];
  private rec(op: string, args: unknown[]) {
    this.calls.push({
      op,
      args,
      comp: this.globalCompositeOperation,
      alpha: this.globalAlpha,
      fill: this.fillStyle,
    });
  }
  save() {
    this.depth++;
    this.stack.push([
      this.globalCompositeOperation,
      this.globalAlpha,
      this.fillStyle,
      this.imageSmoothingEnabled,
    ]);
  }
  restore() {
    this.depth--;
    const s = this.stack.pop();
    if (s)
      [
        this.globalCompositeOperation,
        this.globalAlpha,
        this.fillStyle,
        this.imageSmoothingEnabled,
      ] = s;
  }
  clearRect(...a: number[]) {
    this.rec('clearRect', a);
  }
  fillRect(...a: number[]) {
    this.rec('fillRect', a);
  }
  drawImage(image: unknown, ...a: number[]) {
    this.rec('drawImage', [image, ...a]);
  }
  createRadialGradient() {
    return { addColorStop: () => undefined };
  }
}
class FakeLayer implements R.CityLayer {
  ctx = new FakeCtx();
  width: number;
  height: number;
  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
  }
  getContext() {
    return this.ctx;
  }
}
function install() {
  const made: FakeLayer[] = [];
  R.setCityLayerFactory((w, h) => {
    const l = new FakeLayer(w, h);
    made.push(l);
    return l;
  });
  return made;
}

const W = 800;
const H = 600;
const ZOOM = 2;
const TILE = 16 * ZOOM;
const frame: R.CityFrame = {
  canvasWidth: W,
  canvasHeight: H,
  offsetX: 0,
  offsetY: 0,
  zoom: ZOOM,
  view: { c0: 0, c1: 26, r0: 0, r1: 20 },
};
const outdoorMask = (): M.CityMask =>
  M.buildCityMask(40, 40, new Array(1600).fill(TileType.FLOOR_15));
const inView = (n: number, x0 = 40): Array<{ x: number; y: number }> =>
  Array.from({ length: n }, (_, k) => ({ x: x0 + (k % 20) * 10, y: 60 + Math.floor(k / 20) * 10 }));

test('a clear day draws nothing and creates no surface', () => {
  const made = install();
  const main = new FakeCtx();
  assert.equal(
    R.renderCityLighting(
      main,
      { level: 0, mask: outdoorMask(), lamps: inView(5), agents: inView(5) },
      frame,
    ),
    null,
  );
  assert.equal(
    R.renderCityLighting(main, { level: 0.005, mask: outdoorMask(), lamps: [], agents: [] }, frame),
    null,
  );
  assert.equal(made.length, 0);
  assert.equal(main.calls.length, 0);
});

test('night lays one darkness layer over the scene: clear, fill with the stage colour, then composite once', () => {
  const made = install();
  const main = new FakeCtx();
  const stats = R.renderCityLighting(
    main,
    { level: 1, mask: outdoorMask(), lamps: [], agents: [] },
    frame,
  );
  assert.deepEqual(stats, { lights: 0, glows: 0, indoorRects: 0 });
  assert.equal(made.length, 3); // the darkness layer and the two shared light sprites
  const dark = made[0];
  assert.deepEqual([dark.width, dark.height], [W, H]);
  const clear = dark.ctx.calls.find((c) => c.op === 'clearRect')!;
  assert.deepEqual(clear.args, [0, 0, W, H]);
  const fill = dark.ctx.calls.find((c) => c.op === 'fillRect' && c.comp === 'source-over')!;
  assert.deepEqual(fill.args, [0, 0, W, H]);
  assert.equal(fill.fill, cityRgba(8, 14, 46, 0.58));
  const composites = main.calls.filter((c) => c.op === 'drawImage' && c.args[0] === dark);
  assert.equal(composites.length, 1);
  assert.deepEqual(composites[0].args.slice(1), [0, 0, W, H]);
  assert.equal(composites[0].comp, 'source-over');
  assert.equal(composites[0].alpha, 1);
  // nothing leaks: both contexts are back in their default state with balanced save/restore
  for (const c of [main, dark.ctx]) {
    assert.equal(c.depth, 0);
    assert.equal(c.globalCompositeOperation, 'source-over');
    assert.equal(c.globalAlpha, 1);
  }
});

test('indoor runs in view are punched back to lit, one rectangle per run, in the right place', () => {
  const made = install();
  const tiles = new Array(1600).fill(TileType.FLOOR_15);
  for (const [c, r] of [
    [2, 3],
    [3, 3],
    [4, 3],
    [7, 3],
    [5, 5],
  ])
    tiles[r * 40 + c] = TileType.FLOOR_26;
  const stats = R.renderCityLighting(
    new FakeCtx(),
    { level: 1, mask: M.buildCityMask(40, 40, tiles), lamps: [], agents: [] },
    frame,
  );
  assert.equal(stats!.indoorRects, 3);
  const holes = made[0].ctx.calls.filter(
    (c) => c.op === 'fillRect' && c.comp === 'destination-out',
  );
  assert.deepEqual(
    holes.map((c) => c.args),
    [
      [2 * TILE, 3 * TILE, 3 * TILE, TILE],
      [7 * TILE, 3 * TILE, TILE, TILE],
      [5 * TILE, 5 * TILE, TILE, TILE],
    ],
  );
  for (const h of holes) assert.equal(h.fill, cityRgba(0, 0, 0, 0.85));
});

test('real agents are lit first and the total never exceeds CITY_LIGHT_MAX_LIGHTS', () => {
  install();
  const full = R.renderCityLighting(
    new FakeCtx(),
    { level: 1, mask: outdoorMask(), lamps: inView(10, 200), agents: inView(100) },
    frame,
  );
  assert.equal(full!.lights, 96);
  assert.equal(full!.glows, 0); // the agents used the whole budget: the lamps wait
  install();
  const mixed = R.renderCityLighting(
    new FakeCtx(),
    { level: 1, mask: outdoorMask(), lamps: inView(200, 120), agents: inView(10) },
    frame,
  );
  assert.equal(mixed!.lights, 96);
  assert.equal(mixed!.glows, 86);
});

test('only lights in or near the view are drawn, and lamps glow warmly while agents do not', () => {
  const made = install();
  const main = new FakeCtx();
  const far = { x: 100_000, y: 100_000 };
  const lamp = { x: 100, y: 100 };
  const agent = { x: 200, y: 150 };
  const stats = R.renderCityLighting(
    main,
    { level: 1, mask: outdoorMask(), lamps: [lamp, far], agents: [agent, far] },
    frame,
  );
  assert.deepEqual(stats, { lights: 2, glows: 1, indoorRects: 0 });
  const pools = made[0].ctx.calls.filter(
    (c) => c.op === 'drawImage' && c.comp === 'destination-out',
  );
  assert.equal(pools.length, 2);
  assert.equal(pools[0].alpha, 0.95); // the agent first
  assert.equal(pools[1].alpha, 0.9);
  // the agent's pool is a 3 tile radius around its screen position
  const r = 3 * TILE;
  assert.deepEqual(pools[0].args.slice(1), [200 * ZOOM - r, 150 * ZOOM - r, r * 2, r * 2]);
  const glows = main.calls.filter((c) => c.op === 'drawImage' && c.comp === 'lighter');
  assert.equal(glows.length, 1);
  assert.equal(glows[0].alpha, 0.22 * 1);
});

test('surfaces are reused for the same canvas size and rebuilt only when it changes', () => {
  const made = install();
  const input = { level: 1, mask: outdoorMask(), lamps: [], agents: [] };
  R.renderCityLighting(new FakeCtx(), input, frame);
  R.renderCityLighting(new FakeCtx(), input, frame);
  assert.equal(made.length, 3);
  R.renderCityLighting(new FakeCtx(), input, { ...frame, canvasWidth: 640 });
  assert.equal(made.length, 4); // one new darkness layer; the sprites are shared
});

test('no canvas available means no lighting, never an error', () => {
  R.setCityLayerFactory(() => null);
  assert.equal(
    R.renderCityLighting(
      new FakeCtx(),
      { level: 1, mask: outdoorMask(), lamps: [], agents: [] },
      frame,
    ),
    null,
  );
  R.setCityLayerFactory(null);
});

test("weather's veil on a clear day lays one veil layer, lights the agents and never the lamps", () => {
  const made = install();
  const main = new FakeCtx();
  const veil = { r: 62, g: 78, b: 104, a: 0.2 };
  const stats = R.renderCityLighting(
    main,
    { level: 0, veil, mask: outdoorMask(), lamps: inView(5, 300), agents: inView(2) },
    frame,
  );
  assert.deepEqual(stats, { lights: 2, glows: 0, indoorRects: 0 }); // the agents are lit; the lamps are not drawn by day
  const fills = made[0].ctx.calls.filter((c) => c.op === 'fillRect' && c.comp === 'source-over');
  assert.equal(fills.length, 1);
  assert.equal(fills[0].fill, cityRgba(62, 78, 104, 0.2));
  assert.equal(main.calls.filter((c) => c.op === 'drawImage' && c.comp === 'lighter').length, 0); // no glow
});

test('night under weather lays the stage darkness AND the veil, in that order, over the same holes', () => {
  const made = install();
  const veil = { r: 120, g: 134, b: 160, a: 0.14 };
  const stats = R.renderCityLighting(
    new FakeCtx(),
    { level: 1, veil, mask: outdoorMask(), lamps: inView(3, 300), agents: [] },
    frame,
  );
  assert.deepEqual(stats, { lights: 3, glows: 3, indoorRects: 0 });
  const fills = made[0].ctx.calls
    .filter((c) => c.op === 'fillRect' && c.comp === 'source-over')
    .map((c) => c.fill);
  assert.deepEqual(fills, [cityRgba(8, 14, 46, 0.58), cityRgba(120, 134, 160, 0.14)]);
});

test('no night and no veil draws nothing; a veil of no opacity is no veil', () => {
  const made = install();
  assert.equal(
    R.renderCityLighting(
      new FakeCtx(),
      { level: 0, veil: null, mask: outdoorMask(), lamps: [], agents: [] },
      frame,
    ),
    null,
  );
  assert.equal(
    R.renderCityLighting(
      new FakeCtx(),
      { level: 0, veil: { r: 1, g: 1, b: 1, a: 0 }, mask: outdoorMask(), lamps: [], agents: [] },
      frame,
    ),
    null,
  );
  assert.equal(made.length, 0);
});

test('STRUCTURAL: the city layers are drawn after the scene and BEFORE bubbles, labels and editor chrome', () => {
  // This reads the renderer's source: it shows the ORDER in renderFrame, not that anything was drawn.
  // (The real-App test shows the drawn result: the selected agent's tooltip and the DOM chrome stay legible.)
  const src = fs.readFileSync(path.join(__dirname, '..', 'src/office/engine/renderer.ts'), 'utf-8');
  const frame = src.slice(src.indexOf('export function renderFrame('));
  const at = (needle: string) => {
    const i = frame.indexOf(needle);
    assert.ok(i >= 0, `${needle} is missing from renderFrame`);
    return i;
  };
  const order = [
    at('renderScene('),
    at('renderCityLighting('),
    at('renderCityWeather('),
    at('renderBubbles('),
    at('renderAreaLabels('),
    at('renderGridOverlay('),
  ];
  assert.deepEqual(
    order,
    [...order].sort((a, b) => a - b),
    'renderScene < lighting < weather < bubbles < labels < editor overlays',
  );
});
