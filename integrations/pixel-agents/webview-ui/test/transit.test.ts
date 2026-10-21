/**
 * THE SUBWAY (M3 S3) on the SHIPPED living metropolis, judged by the product's own code: the line is
 * derived from world data; trains actually move, stop at every station and cross over at the termini
 * without ever sharing a track segment; a named resident boards at one station and alights at
 * another; riders are hidden and not hit-testable while aboard; and the Map's living layer draws
 * every resident on the ground and every train.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { AMBIENT_RIDERS_MAX, AMBIENT_ROUTINE_MAX_STOPS } from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import { drawLivingLayer } from '../src/office/livingLayer.ts';
import { deriveTransitLine, TRAIN_LENGTH_TILES } from '../src/office/transit/transitWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const world = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));

let os: OfficeState;
const SIM_SECONDS = 420;
const DT = 0.1;
const overlaps: string[] = [];
beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(world());
  os.setAmbientEnabled(true);
  for (let k = 0; k < SIM_SECONDS / DT; k++) {
    os.update(DT);
    const [a, b] = os.getTrains();
    if (a && b && a.row === b.row && a.state !== 'crossover' && b.state !== 'crossover' && Math.abs(a.x - b.x) < TRAIN_LENGTH_TILES)
      overlaps.push(`t=${(k * DT).toFixed(1)} ${a.x} ${b.x}`);
  }
}, 120_000);

test('the line is derived from the world: two tracks, four named stations west to east', () => {
  const o = new OfficeState(world());
  const line = deriveTransitLine(o.layout, (c, r) => isWalkable(c, r, o.tileMap, o.blockedTiles));
  assert.ok(line);
  assert.equal(line.westRow - line.eastRow, 2);
  assert.deepEqual(line.stations.map((s) => s.name), ['Atrium Station', 'Observatory Station', 'Cathedral Station', 'Market Station']);
  for (const s of line.stations) assert.ok(s.island.length >= 8, `${s.name} island platform`);
});

test('trains actually move: both serve stations and cross over at the termini, never sharing a track segment', () => {
  const t = os.transit!;
  assert.ok(t);
  for (const tr of t.trains) assert.ok(tr.stopsMade >= 4, `${tr.id} made ${tr.stopsMade} stops`);
  assert.ok(t.totals.crossovers >= 2, `${t.totals.crossovers} crossovers`);
  const stationsVisited = new Set(t.events.filter((e) => e.kind === 'arrive').map((e) => e.station));
  assert.equal(stationsVisited.size, 4);
  assert.deepEqual(overlaps, []);
});

test('a named resident boards at one station and alights at another', () => {
  const t = os.transit!;
  const boards = t.events.filter((e) => e.kind === 'board');
  const alights = t.events.filter((e) => e.kind === 'alight');
  assert.ok(boards.length > 0 && alights.length > 0, `${boards.length} boardings, ${alights.length} alightings`);
  const trip = alights.find((a) => boards.some((b) => b.resident === a.resident && b.station !== a.station && b.t < a.t));
  assert.ok(trip, 'a recorded boarding at A and alighting at B by the same resident');
});

test('riders are aboard, not on the ground: hidden from hit-testing and from the Map layer', () => {
  const riding = os.getAmbientResidents().filter((r) => r.state === 'riding');
  for (const r of riding) assert.equal(os.ambient!.residentAt(r.x, r.y)?.id === r.id, false);
  let rects = 0;
  const ctx = { fillStyle: '' as unknown, fillRect: () => void rects++ };
  const counts = drawLivingLayer(ctx, os.getAmbientResidents(), os.getTrains(), 0.25, 0.25);
  const onGround = os.getAmbientResidents().length - riding.length;
  assert.equal(counts.residents, onGround);
  assert.equal(counts.trains, 2);
  assert.equal(rects, onGround + 2);
});

test('riders are a bounded subset, and every routine (riders included) stays within the stop bound', () => {
  const w = os.ambient!;
  assert.ok(w.riders.length > 0 && w.riders.length <= AMBIENT_RIDERS_MAX);
  assert.ok(w.coverage.longestRoutine <= AMBIENT_ROUTINE_MAX_STOPS);
  for (const r of w.residents) assert.ok(r.routine.length <= AMBIENT_ROUTINE_MAX_STOPS, `${r.id}: ${r.routine.length} stops`);
});
