import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import {
  DI_DEFAULT_LAYOUT_FINGERPRINT,
  DI_WORLD_MAX_ROWS,
  MAX_ROWS,
  ZOOM_MAX,
  ZOOM_MIN,
} from '../src/constants.ts';
import {
  aspectBands,
  assignDiZones,
  bandSeats,
  bandsForSlots,
  buildDiWorld,
  cropToContent,
  diFitZoom,
  layoutFingerprint,
  maxBands,
  slotSeats,
  worldRows,
} from '../src/office/layout/diWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const DEFAULT = JSON.parse(
  fs.readFileSync(path.join(__dirname, '../public/assets/default-layout-1.json'), 'utf-8'),
) as OfficeLayout;

test('the pinned fingerprint IS the bundled default layout (only it may be reshaped)', () => {
  assert.equal(layoutFingerprint(DEFAULT), DI_DEFAULT_LAYOUT_FINGERPRINT);
  const custom = { ...DEFAULT, furniture: DEFAULT.furniture.slice(1) };
  assert.notEqual(layoutFingerprint(custom), DI_DEFAULT_LAYOUT_FINGERPRINT);
});

test('fit zoom is INTEGER device pixels from the backing store, clamped', () => {
  // 1440x900 viewport, 420 px panel -> main 1020x900 CSS.
  assert.equal(diFitZoom(1020, 900, 20, 17), 3); // DPR 1
  assert.equal(diFitZoom(2040, 1800, 20, 17), 6); // DPR 2: twice the zoom, never half
  assert.equal(diFitZoom(100000, 100000, 1, 1), ZOOM_MAX);
  assert.equal(diFitZoom(10, 10, 64, 64), ZOOM_MIN);
  assert.equal(diFitZoom(0, 900, 20, 17), ZOOM_MIN);
});

test('the default is cropped to its room: VOID padding dropped, furniture shifted with it', () => {
  const room = cropToContent(DEFAULT);
  assert.equal(DEFAULT.cols, 21);
  assert.equal(DEFAULT.rows, 22);
  assert.equal(room.cols, 20);
  assert.equal(room.rows, 11);
  assert.ok(!room.tiles.includes(TileType.VOID));
  const desk = room.furniture.find((f) => f.type === 'DESK_FRONT')!;
  const orig = DEFAULT.furniture.find((f) => f.uid === desk.uid)!;
  assert.equal(desk.row, orig.row - 10);
  assert.equal(desk.col, orig.col);
});

test('the world SHAPE follows the main area: a near-square area gets a hall, a wide one none', () => {
  const room = cropToContent(DEFAULT);
  assert.equal(aspectBands(room.cols, room.rows, 1020 / 900), 1); // 20x17 ~ 1.18 vs 1.13
  assert.equal(aspectBands(room.cols, room.rows, 860 / 800), 1);
  assert.equal(aspectBands(room.cols, room.rows, 20 / 11), 0); // already the room's shape
  assert.ok(aspectBands(room.cols, room.rows, 0.6) >= 3); // a tall area -> several bands
  assert.ok(worldRows(room.rows, maxBands(room.rows)) <= DI_WORLD_MAX_ROWS);
});

test('Mission zones are HALF-BAND slots: the first Mission holds the room, small Missions share a band; ended zones are released', () => {
  assert.equal(bandSeats(20), 8);
  const seats = slotSeats(20);
  assert.equal(seats, 4);
  const roster = [
    { missionRef: 'A', count: 4 },
    { missionRef: 'B', count: 1 },
    { missionRef: 'C', count: 9 },
  ];
  let a = assignDiZones({ room: null, slots: [] }, roster, 14, seats, 24);
  assert.deepEqual(a, { room: 'A', slots: ['B', 'C', 'C', 'C'] });
  assert.equal(bandsForSlots(a.slots.length), 2);
  // Two small Missions share ONE band (the DPR-1 framing case): no second band forced.
  const small = assignDiZones(
    { room: null, slots: [] },
    [
      { missionRef: 'A', count: 4 },
      { missionRef: 'B', count: 1 },
      { missionRef: 'C', count: 1 },
    ],
    14,
    seats,
    24,
  );
  assert.deepEqual(small, { room: 'A', slots: ['B', 'C'] });
  assert.equal(bandsForSlots(small.slots.length), 1);
  // STICKY: an unchanged roster keeps every zone where it is.
  assert.deepEqual(assignDiZones(a, roster, 14, seats, 24), a);
  // B ends: its slot is released (not kept to look populated).
  a = assignDiZones(a, [roster[0], { missionRef: 'B', count: 0 }, roster[2]], 14, seats, 24);
  assert.deepEqual(a, { room: 'A', slots: [null, 'C', 'C', 'C'] });
  // A newcomer reuses the free slot.
  a = assignDiZones(a, [roster[0], roster[2], { missionRef: 'D', count: 2 }], 14, seats, 24);
  assert.deepEqual(a, { room: 'A', slots: ['D', 'C', 'C', 'C'] });
  // Bounded by the slot limit (12 bands x 2 within 64 rows).
  const big = assignDiZones(
    { room: null, slots: [] },
    [{ missionRef: 'A', count: 500 }],
    14,
    seats,
    24,
  );
  assert.equal(big.slots.length, 24);
});

test('the built world: hall + pods from existing furniture, doorways, labelled Mission slots and mappings', () => {
  const room = cropToContent(DEFAULT);
  const assignment = { room: 'A', slots: ['B', null, 'C'] };
  const { layout, mappings } = buildDiWorld(room, 2, assignment, (ref) => `mn-${ref}`);
  assert.equal(layout.cols, 20);
  assert.equal(layout.rows, worldRows(11, 2));
  assert.equal(layout.tiles.length, layout.cols * layout.rows);
  const wallRow = layout.tiles.slice(11 * 20, 12 * 20);
  assert.ok(wallRow.filter((t) => t !== TileType.WALL).length >= 2);
  const added = layout.furniture.filter((f) => f.uid.startsWith('di-band-'));
  assert.equal(added.length, 2 * 4 * 4);
  for (const f of added)
    assert.ok(f.col >= 1 && f.col <= 18 && f.row >= 13 && f.row < layout.rows, f.uid);
  for (const f of added.filter((x) => x.type === 'CUSHIONED_BENCH')) {
    const t = layout.tiles[f.row * 20 + f.col];
    assert.ok(t !== TileType.WALL && t !== TileType.VOID, f.uid);
  }
  assert.deepEqual(mappings, { A: ['mn-A'], B: ['mn-B'], C: ['mn-C'] });
  const at = (r: number, c: number) => layout.areaTiles![r * 20 + c];
  const bench0 = 11 + 2 + 2; // band 0 bench row
  const bench1 = bench0 + 4; // band 1 bench row
  assert.equal(at(bench0, 2), 'mn-B'); // slot 0 (band 0, left half)
  assert.equal(at(bench0, 8), 'mn-B');
  assert.equal(at(bench0, 12), null); // slot 1 unassigned
  assert.equal(at(bench1, 4), 'mn-C'); // slot 2 (band 1, left half)
  assert.equal(at(bench1, 14), null); // slot 3 absent
  assert.equal(at(5, 5), 'mn-A'); // the room
});

test('no world growth beyond 64 rows', () => {
  const room = cropToContent(DEFAULT);
  const { layout } = buildDiWorld(room, 99, { room: null, slots: [] }, (r) => r);
  assert.ok(layout.rows <= DI_WORLD_MAX_ROWS);
});

test('raising the world ceiling does not multiply the DEFAULT world band capacity', () => {
  // The default world keeps the former 64-row band cap, independent of the editor's world
  // ceiling (MAX_ROWS), so a large custom-world bound never balloons the default office.
  assert.equal(DI_WORLD_MAX_ROWS, 64);
  assert.ok(MAX_ROWS > DI_WORLD_MAX_ROWS);
  const room = cropToContent(DEFAULT);
  assert.equal(maxBands(room.rows), Math.floor((64 - room.rows - 2) / 4));
});
