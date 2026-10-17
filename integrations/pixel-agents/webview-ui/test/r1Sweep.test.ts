/**
 * R1 — the whole-world VISUAL CLEANUP sweep on the SHIPPED living metropolis (the inherited palace
 * included). Each class is defined precisely; each test prints one `[R1]` line (found, with cells) and
 * fails when a class exceeds its accepted residue. Classes:
 * - VOID HOLE: a VOID component that does not touch the world border (a black hole inside the world).
 * - ISOLATED WALL: a wall cell with no orthogonal wall neighbour (a one-tile stub).
 * - DIAGONAL CORNER: two wall cells touching only diagonally (both shared orthogonal cells are not
 *   wall): the auto-tiler draws a gap at that corner.
 * - THRESHOLD MISMATCH: a doorway cell — walkable, walls on both sides along one axis, floor on both
 *   sides along the other — whose floor PATTERN or COLOUR equals NEITHER side it joins (a third tone
 *   in the door).
 * - BLOCKED DOOR: a doorway cell (by tiles) that furniture makes unwalkable.
 * - DARK FLOOR: a floor cell coloured at brightness <= -80 (reads as a black bar; the S2b rule).
 * - CROSSING: a zebra-crossing component with an unwalkable cell, or any unwalkable LANDING cell (the
 *   cells just beyond its two ends, across the road), the same rule the world builder's R1 pass clears.
 * Room routes (every designed room reachable and on a routine) are covered by livingWorld.test.ts.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { OfficeState } from '../src/office/engine/officeState.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const ZEBRA = 27;
const ROAD = new Set([15, 16, 17]);
let L: OfficeLayout;
let os: OfficeState;
let walk: (c: number, r: number) => boolean;
const report = (k: string, v: unknown) => console.log(`[R1] ${JSON.stringify({ [k]: v })}`);

beforeAll(() => {
  loadBundledCatalog();
  L = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout;
  os = new OfficeState(L);
  walk = (c, r) => c >= 0 && r >= 0 && c < L.cols && r < L.rows && isWalkable(c, r, os.tileMap, os.blockedTiles);
}, 300_000);

const at = (c: number, r: number) => (c < 0 || r < 0 || c >= L.cols || r >= L.rows ? -1 : L.tiles[r * L.cols + c]);
const isWall = (c: number, r: number) => at(c, r) === TileType.WALL;
const isFloor = (c: number, r: number) => {
  const t = at(c, r);
  return t > 0 && t !== TileType.VOID;
};
const colour = (c: number, r: number) => JSON.stringify(L.tileColors?.[r * L.cols + c] ?? null);

/** Every cell of a class, as [col,row]. */
export function sweepWorld(): Record<string, Array<[number, number]>> {
  const out: Record<string, Array<[number, number]>> = { voidHole: [], isolatedWall: [], diagonalCorner: [], thresholdPattern: [], thresholdColour: [], blockedDoor: [], darkFloor: [], crossing: [] };
  const { cols, rows } = L;
  // VOID holes
  const seen = new Uint8Array(cols * rows);
  for (let i = 0; i < cols * rows; i++) {
    if (seen[i] || L.tiles[i] !== TileType.VOID) continue;
    const comp: number[] = [];
    let border = false;
    const q = [i];
    seen[i] = 1;
    while (q.length) {
      const p = q.pop()!;
      comp.push(p);
      const c = p % cols;
      const r = (p - c) / cols;
      if (c === 0 || r === 0 || c === cols - 1 || r === rows - 1) border = true;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nc = c + dc;
        const nr = r + dr;
        if (nc < 0 || nr < 0 || nc >= cols || nr >= rows) continue;
        const n = nr * cols + nc;
        if (!seen[n] && L.tiles[n] === TileType.VOID) {
          seen[n] = 1;
          q.push(n);
        }
      }
    }
    if (!border) for (const p of comp) out.voidHole.push([p % cols, Math.floor(p / cols)]);
  }
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) {
      if (isWall(c, r)) {
        if (!isWall(c - 1, r) && !isWall(c + 1, r) && !isWall(c, r - 1) && !isWall(c, r + 1)) out.isolatedWall.push([c, r]);
        // diagonal-only contact with the cell down-right / down-left
        if (isWall(c + 1, r + 1) && !isWall(c + 1, r) && !isWall(c, r + 1)) out.diagonalCorner.push([c, r]);
        if (isWall(c - 1, r + 1) && !isWall(c - 1, r) && !isWall(c, r + 1)) out.diagonalCorner.push([c, r]);
        continue;
      }
      if (!isFloor(c, r)) continue;
      const col = L.tileColors?.[r * cols + c];
      if (col && typeof col.b === 'number' && col.b <= -80) out.darkFloor.push([c, r]);
      // doorway by tiles: walls on one axis, floor on the other
      const horizontalGap = isWall(c - 1, r) && isWall(c + 1, r) && isFloor(c, r - 1) && isFloor(c, r + 1);
      const verticalGap = isWall(c, r - 1) && isWall(c, r + 1) && isFloor(c - 1, r) && isFloor(c + 1, r);
      if (!horizontalGap && !verticalGap) continue;
      const [a, b] = horizontalGap ? [[c, r - 1], [c, r + 1]] : [[c - 1, r], [c + 1, r]];
      const t = at(c, r);
      if (t !== at(a[0], a[1]) && t !== at(b[0], b[1])) out.thresholdPattern.push([c, r]);
      const k = colour(c, r);
      if (k !== colour(a[0], a[1]) && k !== colour(b[0], b[1])) out.thresholdColour.push([c, r]);
      if (!walk(c, r)) out.blockedDoor.push([c, r]);
    }
  // crossings: zebra components
  const zseen = new Uint8Array(cols * rows);
  for (let i = 0; i < cols * rows; i++) {
    if (zseen[i] || L.tiles[i] !== ZEBRA) continue;
    const comp: number[] = [];
    const q = [i];
    zseen[i] = 1;
    while (q.length) {
      const p = q.pop()!;
      comp.push(p);
      const c = p % cols;
      const r = (p - c) / cols;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const n = (r + dr) * cols + (c + dc);
        if (c + dc >= 0 && r + dr >= 0 && c + dc < cols && r + dr < rows && !zseen[n] && L.tiles[n] === ZEBRA) {
          zseen[n] = 1;
          q.push(n);
        }
      }
    }
    const cells = comp.map((p) => [p % cols, Math.floor(p / cols)] as [number, number]);
    const blocked = cells.some(([c, r]) => !walk(c, r));
    const sideOk = (dc: number, dr: number) => cells.some(([c, r]) => walk(c + dc, r + dr) && !ROAD.has(at(c + dc, r + dr)) && at(c + dc, r + dr) !== ZEBRA);
    const cs = cells.map(([c]) => c);
    const rs = cells.map(([, r]) => r);
    const [c0, c1, r0, r1] = [Math.min(...cs), Math.max(...cs), Math.min(...rs), Math.max(...rs)];
    const roadish = (c: number, r: number) => at(c, r) === ZEBRA || ROAD.has(at(c, r));
    const vertical = [r0 - 1, r1 + 1].every((r) => at(c0, r) >= 0 && !roadish(c0, r));
    const landings: Array<[number, number]> = [];
    if (vertical) for (let c = c0; c <= c1; c++) landings.push([c, r0 - 1], [c, r1 + 1]);
    else for (let r = r0; r <= r1; r++) landings.push([c0 - 1, r], [c1 + 1, r]);
    const landingBlocked = landings.some(([c, r]) => at(c, r) >= 0 && !walk(c, r));
    const joins = (sideOk(0, -1) && sideOk(0, 1)) || (sideOk(-1, 0) && sideOk(1, 0));
    if (blocked || !joins || landingBlocked) {
      out.crossing.push(cells[0]);
      const blockers = cells.filter(([c, r]) => !walk(c, r));
      crossingDetail.push({ first: cells[0], cells: cells.length, blockedCells: blockers.slice(0, 6), blockedBy: blockers.slice(0, 3).map(([c, r]) => (L.furniture ?? []).filter((f) => c >= f.col && r >= f.row && c < f.col + 8 && r < f.row + 8).map((f) => `${f.type}@${f.col},${f.row}`).slice(0, 3)), sides: { up: sideOk(0, -1), down: sideOk(0, 1), left: sideOk(-1, 0), right: sideOk(1, 0) } });
    }
  }
  return out;
}

const crossingDetail: unknown[] = [];
let found: Record<string, Array<[number, number]>>;
// DELIBERATELY LEFT (recorded in LIVING-WORLD-BUILD.json r1.deliberatelyLeft), asserted cell for cell
const COLONNADE_PIERS = [[33, 251], [37, 251], [41, 251], [45, 251], [109, 251], [113, 251], [117, 251], [121, 251]];
const PALACE_PAINTING_GAPS = [[82, 71], [83, 71], [65, 98], [66, 98]];
const sorted = (a: number[][]) => JSON.stringify([...a].map((x) => x.join(',')).sort());

test('R1 sweep: measure every class on the shipped world', () => {
  found = sweepWorld();
  report('counts', Object.fromEntries(Object.entries(found).map(([k, v]) => [k, v.length])));
  for (const [k, v] of Object.entries(found)) if (v.length) report(k, v.slice(0, 60));
  for (const d of crossingDetail) report('crossingDetail', d);
  assert.ok(found);
});

test('R1: no VOID hole, diagonal corner, threshold pattern or colour mismatch, dark floor, or unclean crossing', () => {
  for (const k of ['voidHole', 'diagonalCorner', 'thresholdPattern', 'thresholdColour', 'darkFloor', 'crossing']) assert.deepEqual(found[k], [], k);
});

test('R1: the only isolated walls are the designed colonnade piers, and the only blocked doorways the palace painting gaps', () => {
  assert.equal(sorted(found.isolatedWall), sorted(COLONNADE_PIERS));
  assert.equal(sorted(found.blockedDoor), sorted(PALACE_PAINTING_GAPS));
});
