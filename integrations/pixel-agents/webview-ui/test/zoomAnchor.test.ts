/**
 * DEFECT S-N1 — a user zoom in a LARGE world keeps the world point under its ANCHOR.
 *
 * The pan is device pixels from the centred map, so the world point at the viewport centre is
 * C/2 - pan/zoom: a zoom that KEPT the pan slid the view toward the world's middle — zooming in on
 * the far North Shore coast landed in the city. panForZoomAroundPoint re-derives the pan so the
 * world point under the anchor (the viewport centre for the zoom buttons, the cursor for
 * Ctrl+wheel) stays under it, then clamps at the new zoom. The clamp may legitimately move the
 * view, but only toward the interior (never an edge gap instead).
 *
 * Everything is measured through the renderer's own projection (projection.ts mapOffset, whose
 * inverse is viewportCentreWorldPoint), on the shipped 352 x 224 metropolis grid, at DPR 1 and 2.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import { TILE_SIZE, ZOOM_MAX, ZOOM_MIN } from '../src/constants.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import {
  clampPanToWorld,
  frameWorldView,
  isLargeWorld,
  panForZoomAroundPoint,
  panToWorldPoint,
  viewportCentreWorldPoint,
} from '../src/office/layout/worldViews.ts';
import { mapOffset } from '../src/office/projection.ts';
import type { OfficeLayout } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const METRO = JSON.parse(
  fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf-8'),
) as OfficeLayout;
const { cols: COLS, rows: ROWS } = METRO;

/** Footprints straight from the bundled manifests (groups flattened, `:left` = its side). */
function manifestFootprints(): FootprintOf {
  const map = new Map<string, { w: number; h: number; seat: boolean }>();
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const visit = (node: Record<string, unknown>, category: string) => {
    const cat = (node.category as string | undefined) ?? category;
    if (node.type === 'asset')
      map.set(node.id as string, {
        w: node.footprintW as number,
        h: node.footprintH as number,
        seat: cat === 'chairs',
      });
    for (const m of (node.members as Record<string, unknown>[] | undefined) ?? []) visit(m, cat);
  };
  for (const item of fs.readdirSync(dir)) {
    const file = path.join(dir, item, 'manifest.json');
    if (fs.existsSync(file)) visit(JSON.parse(fs.readFileSync(file, 'utf-8')), '');
  }
  return (type) => map.get(type.replace(/:left$/, '')) ?? null;
}
const footprintOf = manifestFootprints();

/** The office canvas at DPR 1 and DPR 2 (same CSS size: twice the backing store). */
const CANVASES = [
  { w: 1152, h: 945, dpr: 1 },
  { w: 2304, h: 1890, dpr: 2 },
] as const;
/** The North Shore coast probe: tile (320, 40), its centre in world sprite pixels. */
const PROBE = { x: (320 + 0.5) * TILE_SIZE, y: (40 + 0.5) * TILE_SIZE };
/** An interior probe (River Valley Farmland) that no zoom from ZOOM_MIN up ever clamps. */
const INTERIOR = { x: (240 + 0.5) * TILE_SIZE, y: (150 + 0.5) * TILE_SIZE };
/** World-pixel tolerance for the point under the anchor over a whole zoom sequence: one sprite
 *  pixel (each step can be off by half a device pixel, the renderer rounding the pan). */
const TOL = 1;
const EPS = 1e-9;
const WORLD_MID = { x: (COLS * TILE_SIZE) / 2, y: (ROWS * TILE_SIZE) / 2 };

type Pan = { x: number; y: number };
type Canvas = (typeof CANVASES)[number];

/** The world point under a canvas point, through the renderer's projection. */
function pointUnder(c: Canvas, pan: Pan, zoom: number, ax: number, ay: number) {
  const { offsetX, offsetY } = mapOffset(c.w, c.h, COLS, ROWS, zoom, pan.x, pan.y);
  return { x: (ax - offsetX) / zoom, y: (ay - offsetY) / zoom };
}
const centreOf = (c: Canvas, pan: Pan, zoom: number) =>
  viewportCentreWorldPoint(pan.x, pan.y, zoom, COLS, ROWS, c.w, c.h);

/**
 * The independent oracle for one zoom step: the pan that would put the world point under the
 * anchor back under it at `to` (UNCLAMPED, through mapOffset's frame), and what the world clamp
 * makes of it. The clamp is ACTIVE on an axis iff it changed that axis.
 */
function oracle(c: Canvas, pan: Pan, from: number, to: number, ax: number, ay: number) {
  const world = pointUnder(c, pan, from, ax, ay);
  const unclamped = {
    x: ax - world.x * to - Math.floor((c.w - COLS * TILE_SIZE * to) / 2),
    y: ay - world.y * to - Math.floor((c.h - ROWS * TILE_SIZE * to) / 2),
  };
  const clamped = clampPanToWorld(unclamped.x, unclamped.y, to, COLS, ROWS, c.w, c.h);
  return {
    world,
    unclamped,
    clamped,
    activeX: Math.abs(clamped.x - unclamped.x) > EPS,
    activeY: Math.abs(clamped.y - unclamped.y) > EPS,
  };
}

/**
 * Walk a zoom sequence about one anchor and check EVERY step against the oracle:
 *   - the result IS the clamped oracle pan;
 *   - on an axis the clamp left alone, the point under the anchor survives the step (half a
 *     device pixel of pan rounding) and stays within TOL of where the sequence started;
 *   - on an axis the clamp moved, the point under the anchor moved only toward the interior.
 * Returns the final pan and whether any step clamped.
 */
function walk(
  c: Canvas,
  start: Pan,
  zooms: number[],
  ax: number,
  ay: number,
  label: string,
): { pan: Pan; clamped: boolean } {
  let pan = start;
  let clamped = false;
  const origin = pointUnder(c, start, zooms[0], ax, ay);
  for (let i = 1; i < zooms.length; i++) {
    const [from, to] = [zooms[i - 1], zooms[i]];
    const ctx = `${label} @${c.dpr}x zoom ${from}->${to}`;
    const o = oracle(c, pan, from, to, ax, ay);
    const next = panForZoomAroundPoint(pan.x, pan.y, from, to, COLS, ROWS, c.w, c.h, ax, ay);
    assert.ok(
      Math.abs(next.x - o.clamped.x) <= EPS && Math.abs(next.y - o.clamped.y) <= EPS,
      `${ctx}: pan ${JSON.stringify(next)} is not the clamped oracle ${JSON.stringify(o.clamped)}`,
    );
    const after = pointUnder(c, next, to, ax, ay);
    for (const axis of ['x', 'y'] as const) {
      const active = axis === 'x' ? o.activeX : o.activeY;
      if (!active) {
        const step = Math.abs(after[axis] - o.world[axis]);
        assert.ok(step <= 0.5 / to + EPS, `${ctx}: ${axis} moved ${step} px in one step`);
        const drift = Math.abs(after[axis] - origin[axis]);
        assert.ok(drift <= TOL, `${ctx}: ${axis} drifted ${drift} px from the start`);
      } else {
        clamped = true;
        // the clamp only ever pulls the view toward the world's interior
        const unclampedPoint = pointUnder(c, o.unclamped, to, ax, ay)[axis];
        assert.ok(
          Math.abs(after[axis] - WORLD_MID[axis]) <=
            Math.abs(unclampedPoint - WORLD_MID[axis]) + EPS,
          `${ctx}: clamp moved ${axis} away from the interior`,
        );
        const sameSide =
          Math.sign(after[axis] - WORLD_MID[axis]) === Math.sign(o.world[axis] - WORLD_MID[axis]) ||
          Math.abs(after[axis] - WORLD_MID[axis]) <= EPS;
        assert.ok(sameSide, `${ctx}: clamp threw ${axis} across the world's middle`);
        assert.ok(
          Math.abs(after[axis] - WORLD_MID[axis]) <=
            Math.abs(o.world[axis] - WORLD_MID[axis]) + 0.5 / to + EPS,
          `${ctx}: ${axis} under the anchor did not move toward the interior`,
        );
      }
    }
    pan = next;
  }
  return { pan, clamped };
}

const range = (from: number, to: number): number[] => {
  const step = to >= from ? 1 : -1;
  const out: number[] = [];
  for (let z = from; step > 0 ? z <= to : z >= to; z += step) out.push(z);
  return out;
};

/** The office view's usable zoom — what a Map jump lands at (App.jumpToWorldPoint). */
const z0Of = (c: Canvas) => frameWorldView('office', METRO, footprintOf, c.w, c.h, c.dpr)!.zoom;

test('the metropolis grid is a large world (S-N1 applies)', () => {
  assert.deepEqual([COLS, ROWS], [352, 224]);
  assert.ok(isLargeWorld(COLS, ROWS));
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    assert.ok(Number.isInteger(z0) && z0 >= ZOOM_MIN && z0 <= ZOOM_MAX, `@${c.dpr}x z0 ${z0}`);
  }
});

test('the jump to the North Shore probe: as close as the clamp allows at z0', () => {
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    const start = panToWorldPoint(PROBE.x, PROBE.y, z0, COLS, ROWS, c.w, c.h);
    const centre = centreOf(c, start, z0);
    // y is free; x is pinned to the east edge (half a viewport in from the coast)
    assert.ok(Math.abs(centre.y - PROBE.y) <= TOL, `@${c.dpr}x y ${centre.y}`);
    const eastmost = COLS * TILE_SIZE - c.w / 2 / z0;
    assert.ok(Math.abs(centre.x - Math.min(PROBE.x, eastmost)) <= TOL, `@${c.dpr}x x ${centre.x}`);
    assert.equal(Math.floor(centre.x / TILE_SIZE) >= 308, true, 'still the North Shore columns');
  }
});

test('ZOOM BUTTONS (centre anchor): every step to ZOOM_MAX and to ZOOM_MIN keeps the centre, unless the clamp is active', () => {
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    const start = panToWorldPoint(PROBE.x, PROBE.y, z0, COLS, ROWS, c.w, c.h);
    const startCentre = centreOf(c, start, z0);
    const up = walk(c, start, range(z0, ZOOM_MAX), c.w / 2, c.h / 2, 'buttons in');
    assert.equal(up.clamped, false, `@${c.dpr}x zooming IN never needs the clamp`);
    const upCentre = centreOf(c, up.pan, ZOOM_MAX);
    assert.ok(
      Math.abs(upCentre.x - startCentre.x) <= TOL && Math.abs(upCentre.y - startCentre.y) <= TOL,
      `@${c.dpr}x at ZOOM_MAX still on the start point`,
    );
    walk(c, start, range(z0, ZOOM_MIN), c.w / 2, c.h / 2, 'buttons out');
  }
});

test('ZOOM BUTTONS: the coast probe zoomed out to ZOOM_MIN at DPR 2 IS clamped — inward, never an edge gap', () => {
  const c = CANVASES[1];
  const z0 = z0Of(c);
  if (z0 === ZOOM_MIN) return; // nothing below z0 to zoom out to
  const start = panToWorldPoint(PROBE.x, PROBE.y, z0, COLS, ROWS, c.w, c.h);
  const out = walk(c, start, range(z0, ZOOM_MIN), c.w / 2, c.h / 2, 'buttons out');
  assert.equal(out.clamped, true, 'the wider view would leave a gap past the east coast');
  const { offsetX } = mapOffset(c.w, c.h, COLS, ROWS, ZOOM_MIN, out.pan.x, out.pan.y);
  assert.ok(offsetX + COLS * TILE_SIZE * ZOOM_MIN >= c.w, 'no gap beyond the east edge');
  assert.ok(offsetX <= 0, 'no gap before the west edge');
});

test('ZOOM BUTTONS: a full in/out round trip returns the original centre', () => {
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    for (const [name, point] of [
      ['coast probe', PROBE],
      ['interior probe', INTERIOR],
    ] as const) {
      const start = panToWorldPoint(point.x, point.y, z0, COLS, ROWS, c.w, c.h);
      const startCentre = centreOf(c, start, z0);
      const zooms = [...range(z0, ZOOM_MAX), ...range(ZOOM_MAX - 1, z0)];
      const back = walk(c, start, zooms, c.w / 2, c.h / 2, `${name} round trip`).pan;
      const end = centreOf(c, back, z0);
      assert.ok(
        Math.abs(end.x - startCentre.x) <= TOL && Math.abs(end.y - startCentre.y) <= TOL,
        `@${c.dpr}x ${name}: ${JSON.stringify(end)} vs ${JSON.stringify(startCentre)}`,
      );
    }
    // the interior probe never touches the clamp, even through ZOOM_MIN and back
    const start = panToWorldPoint(INTERIOR.x, INTERIOR.y, z0, COLS, ROWS, c.w, c.h);
    const zooms = [
      ...range(z0, ZOOM_MIN),
      ...range(ZOOM_MIN + 1, ZOOM_MAX),
      ...range(ZOOM_MAX - 1, z0),
    ];
    const trip = walk(c, start, zooms, c.w / 2, c.h / 2, 'interior full trip');
    assert.equal(trip.clamped, false, `@${c.dpr}x interior probe clamped`);
  }
});

test('CTRL+WHEEL (cursor anchor): the world point under the cursor survives every step', () => {
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    // a cursor well off-centre: left fifth, lower quarter of the canvas (device px)
    const ax = Math.round(c.w * 0.2);
    const ay = Math.round(c.h * 0.75);
    for (const point of [PROBE, INTERIOR]) {
      const start = panToWorldPoint(point.x, point.y, z0, COLS, ROWS, c.w, c.h);
      const under = pointUnder(c, start, z0, ax, ay);
      const up = walk(c, start, range(z0, ZOOM_MAX), ax, ay, 'wheel in');
      assert.equal(up.clamped, false, `@${c.dpr}x zooming IN about the cursor never clamps`);
      const atMax = pointUnder(c, up.pan, ZOOM_MAX, ax, ay);
      assert.ok(
        Math.abs(atMax.x - under.x) <= TOL && Math.abs(atMax.y - under.y) <= TOL,
        `@${c.dpr}x the cursor's world point survived to ZOOM_MAX`,
      );
      // and back out about the same cursor (the clamp, if any, is checked step by step)
      walk(c, up.pan, range(ZOOM_MAX, z0), ax, ay, 'wheel out');
      // the cursor anchor is NOT the centre: the same zoom about the centre lands elsewhere
      const viaCentre = panForZoomAroundPoint(start.x, start.y, z0, z0 + 1, COLS, ROWS, c.w, c.h);
      const viaCursor = panForZoomAroundPoint(
        start.x,
        start.y,
        z0,
        z0 + 1,
        COLS,
        ROWS,
        c.w,
        c.h,
        ax,
        ay,
      );
      assert.notDeepEqual(viaCursor, viaCentre);
    }
  }
});

test('THE DEFECT, quantified: keeping the pan across one zoom step would drift > 10 tiles off the col-320 probe', () => {
  for (const c of CANVASES) {
    const z0 = z0Of(c);
    const start = panToWorldPoint(PROBE.x, PROBE.y, z0, COLS, ROWS, c.w, c.h);
    const startCentre = centreOf(c, start, z0);
    // OLD: pan unchanged, the renderer only clamping it at the new zoom
    const kept = clampPanToWorld(start.x, start.y, z0 + 1, COLS, ROWS, c.w, c.h);
    const oldCentre = centreOf(c, kept, z0 + 1);
    const oldDriftTiles = Math.abs(oldCentre.x - startCentre.x) / TILE_SIZE;
    const offProbeTiles = Math.abs(oldCentre.x - PROBE.x) / TILE_SIZE;
    assert.ok(oldDriftTiles > 10, `@${c.dpr}x old drift ${oldDriftTiles} tiles`);
    assert.ok(offProbeTiles > 10, `@${c.dpr}x old centre ${offProbeTiles} tiles off the probe`);
    assert.ok(Math.floor(oldCentre.x / TILE_SIZE) < 308, 'the old zoom left the North Shore');
    // NEW: the centre stays put
    const fixed = panForZoomAroundPoint(start.x, start.y, z0, z0 + 1, COLS, ROWS, c.w, c.h);
    const newCentre = centreOf(c, fixed, z0 + 1);
    assert.ok(Math.abs(newCentre.x - startCentre.x) <= TOL, `@${c.dpr}x new centre moved`);
  }
});
