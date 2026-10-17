/**
 * R10 / C3 — THE MAP'S RESIDENT DOTS MOVE (lead1's adjudication). transit.test.ts checks which markers
 * the living layer draws (membership); this checks WHERE: a named resident's drawn Map coordinates,
 * read from the rectangles drawLivingLayer actually produces, change between two redraws after the
 * simulation advances, by the resident's own world movement times the Map scale. A layer that drew the
 * right residents at cached, fixed or home-tile positions would fail here.
 * Each redraw passes OfficeState's live resident and train lists, exactly as WorldMap's draw does.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { AMBIENT_JOB_COLORS, WORLD_MAP_RESIDENT_DOT_PX } from '../src/constants.ts';
import type { AmbientResident } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { drawLivingLayer } from '../src/office/livingLayer.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TILE_SIZE } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
let o: OfficeState;
beforeAll(() => {
  loadBundledCatalog();
  o = new OfficeState(JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
  o.setAmbientEnabled(true);
  for (let k = 0; k < 5 * 60; k++) o.update(1 / 60); // 5 s, so residents are under way
}, 300_000);

interface Rect {
  fill: unknown;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One Map redraw of the living layer, recording every rectangle it fills. Scale as WorldMap computes it
 *  (canvas px / world px) for a 1712 px-wide Map. */
function redraw(residents: readonly AmbientResident[]) {
  const sx = 1712 / (o.layout.cols * TILE_SIZE);
  const sy = Math.round((1712 * o.layout.rows) / o.layout.cols) / (o.layout.rows * TILE_SIZE);
  const rects: Rect[] = [];
  const ctx = {
    fillStyle: '' as unknown,
    fillRect(x: number, y: number, w: number, h: number) {
      rects.push({ fill: this.fillStyle, x, y, w, h });
    },
  };
  const counts = drawLivingLayer(ctx, residents, o.getTrains(), sx, sy);
  return { rects, counts, sx, sy };
}

/** The dot the layer drew for `id`: dots come first, one per grounded resident in list order. */
function dotOf(id: string, residents: readonly AmbientResident[], d: ReturnType<typeof redraw>): Rect {
  const grounded = residents.filter((r) => r.state !== 'riding');
  assert.equal(d.counts.residents, grounded.length, 'one dot per grounded resident');
  const k = grounded.findIndex((r) => r.id === id);
  assert.ok(k >= 0, `${id} is grounded, so it has a dot`);
  const dot = d.rects[k];
  assert.equal(dot.w, WORLD_MAP_RESIDENT_DOT_PX);
  assert.equal(dot.h, WORLD_MAP_RESIDENT_DOT_PX);
  return dot;
}

/** Where the layer must draw a resident at its CURRENT position (the layer's own formula). */
const expectedAt = (r: AmbientResident, sx: number, sy: number) => ({
  x: Math.round(r.x * sx - WORLD_MAP_RESIDENT_DOT_PX / 2),
  y: Math.round((r.y - TILE_SIZE / 2) * sy - WORLD_MAP_RESIDENT_DOT_PX / 2),
});

test("a named resident's DRAWN Map coordinates change between two redraws after the simulation advances", () => {
  // the named resident: the first one walking now (the roster and its routines are deterministic)
  const before = o.getAmbientResidents();
  const r = before.find((x) => x.state === 'walk');
  assert.ok(r, 'precondition: a resident is walking');
  const id = r.id;
  const world0 = { x: r.x, y: r.y };
  const d0 = redraw(before);
  const dot0 = dotOf(id, before, d0);
  assert.equal(dot0.fill, AMBIENT_JOB_COLORS[r.job], 'the dot carries its job colour');
  assert.deepEqual({ x: dot0.x, y: dot0.y }, expectedAt(r, d0.sx, d0.sy), 'redraw 1 is at its CURRENT position');
  // advance one Map tick (500 ms) of simulation
  for (let k = 0; k < 30; k++) o.update(1 / 60);
  const after = o.getAmbientResidents();
  const r1 = after.find((x) => x.id === id)!;
  assert.notEqual(r1.state, 'riding', 'precondition: still on foot, so still a dot');
  const moved = Math.hypot(r1.x - world0.x, r1.y - world0.y);
  assert.ok(moved > 0, `precondition: it moved in the world (${moved.toFixed(2)} world px)`);
  const d1 = redraw(after);
  const dot1 = dotOf(id, after, d1);
  assert.deepEqual({ x: dot1.x, y: dot1.y }, expectedAt(r1, d1.sx, d1.sy), 'redraw 2 is at its NEW current position');
  // THE ASSERTION: the drawn marker moved, by the world movement times the Map scale
  const dx = dot1.x - dot0.x;
  const dy = dot1.y - dot0.y;
  assert.ok(dx !== 0 || dy !== 0, `the drawn dot moved (${dx}, ${dy}) canvas px`);
  assert.ok(Math.abs(dx - (r1.x - world0.x) * d0.sx) <= 1 && Math.abs(dy - (r1.y - world0.y) * d0.sy) <= 1, 'by the world movement times the Map scale');
  // CONTROL: a STALE layer (drawing a copy of the residents frozen at redraw 1) shows NO movement at
  // redraw 2, so the assertion above discriminates exactly the failure lead1 named
  const frozen = before.map((x) => ({ ...x }));
  for (const x of frozen) if (x.id === id) Object.assign(x, world0, { state: 'walk' });
  const stale = dotOf(id, frozen, redraw(frozen));
  assert.deepEqual({ x: stale.x, y: stale.y }, { x: dot0.x, y: dot0.y }, 'control: a frozen copy draws the old position');
  console.log(`[R10] ${JSON.stringify({ resident: id, job: r.job, world: { from: world0, to: { x: r1.x, y: r1.y } }, drawn: { from: [dot0.x, dot0.y], to: [dot1.x, dot1.y] }, deltaCanvasPx: [dx, dy], scale: [d0.sx, d0.sy], simulatedSeconds: 0.5 })}`);
});
