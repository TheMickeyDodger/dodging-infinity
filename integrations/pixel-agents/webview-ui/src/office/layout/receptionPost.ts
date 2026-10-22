/**
 * THE RECEPTION POST (DOM-free, unit-tested): where the physical reception desk is and where the
 * decorative receptionist stands, derived from the layout's OWN furniture — no extra layout field,
 * so a saved layout, an export and an import stay exactly the editor's shape.
 *
 * The shipped worlds already carry a reception desk: the lobby desk under one fixed uid (see
 * DI_RECEPTION_DESK_UID). A layout without it (the bundled default, a custom world, a desk the user
 * deleted) simply has no physical entrance; the sidebar entry still opens the same inbox.
 *
 * The receptionist is decoration: it grants no authority, is not an agent and takes no seat.
 */
import {
  DI_RECEPTION_BENCH_UID,
  DI_RECEPTION_DESK_TYPE,
  DI_RECEPTION_DESK_UID,
} from '../../constants.js';
import type { OfficeLayout } from '../types.js';
import { Direction } from '../types.js';
import type { FootprintOf } from './worldViews.js';

export interface ReceptionPost {
  /** The desk's uid and its top-left tile and size (tiles). */
  deskUid: string;
  deskCol: number;
  deskRow: number;
  deskW: number;
  deskH: number;
  /** The tile the receptionist stands on: walkable, next to the desk, never a seat. */
  standCol: number;
  standRow: number;
  /**
   * Whether the stand tile may be BLOCKED for real agents, pets and residents (occupied by the receptionist). True only when
   * blocking it keeps every walkable neighbour of it connected to the others — a tile that is the only way through a lobby is
   * left open (residents still yield to the receptionist there). False when the caller supplied no connectivity test.
   */
  tileBlocked: boolean;
}

/**
 * The reception post of `layout`, or null when it has no reception desk or nowhere to stand.
 * Deterministic: the same layout always yields the same tile. The stand tile is the first walkable
 * one of: behind the desk (north, centred), then north-left, north-right, west, east — preferring one that
 * `canBlock` accepts (the caller's test that blocking it disconnects nothing); if none is acceptable the first
 * walkable one is used and `tileBlocked` is false.
 */
export function findReceptionPost(
  layout: Pick<OfficeLayout, 'furniture'>,
  footprintOf: FootprintOf,
  isWalkable: (col: number, row: number) => boolean,
  canBlock?: (col: number, row: number) => boolean,
): ReceptionPost | null {
  const desk = layout.furniture.find(
    (f) => f.uid === DI_RECEPTION_DESK_UID && f.type === DI_RECEPTION_DESK_TYPE,
  );
  if (!desk) return null;
  const fp = footprintOf(desk.type);
  if (!fp) return null;
  const mid = desk.col + Math.floor(fp.w / 2);
  const candidates: Array<[number, number]> = [
    [mid, desk.row - 1],
    [desk.col, desk.row - 1],
    [desk.col + fp.w - 1, desk.row - 1],
    [desk.col - 1, desk.row],
    [desk.col + fp.w, desk.row],
  ];
  const walkable = candidates.filter(([c, r]) => isWalkable(c, r));
  if (walkable.length === 0) return null;
  const blockable = canBlock ? walkable.find(([c, r]) => canBlock(c, r)) : undefined;
  const stand = blockable ?? walkable[0];
  return {
    deskUid: desk.uid,
    deskCol: desk.col,
    deskRow: desk.row,
    deskW: fp.w,
    deskH: fp.h,
    standCol: stand[0],
    standRow: stand[1],
    tileBlocked: blockable !== undefined,
  };
}

const NEIGHBOUR_STEPS: ReadonlyArray<readonly [number, number]> = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
];

/**
 * Whether removing the walkable tile (col,row) from the walkable grid leaves its walkable neighbours connected to each other
 * (so a character parked there can never cut a lobby in two). An early-exit breadth-first search from one neighbour over the
 * grid, bounded by `cols * rows` visited tiles; run ONCE per layout build. A tile with fewer than two walkable neighbours
 * disconnects nothing.
 */
export function keepsNeighboursConnected(
  col: number,
  row: number,
  isWalkable: (col: number, row: number) => boolean,
  cols: number,
  rows: number,
): boolean {
  const inGrid = (c: number, r: number): boolean => c >= 0 && r >= 0 && c < cols && r < rows;
  const neighbours = NEIGHBOUR_STEPS.map(([dc, dr]) => [col + dc, row + dr] as const).filter(
    ([c, r]) => inGrid(c, r) && isWalkable(c, r),
  );
  if (neighbours.length < 2) return true;
  const want = new Set(neighbours.map(([c, r]) => r * cols + c));
  const seen = new Set<number>([row * cols + col]);
  const start = neighbours[0][1] * cols + neighbours[0][0];
  seen.add(start);
  want.delete(start);
  const queue: number[] = [start];
  for (let i = 0; i < queue.length && want.size > 0; i++) {
    const at = queue[i];
    const c = at % cols;
    const r = (at - c) / cols;
    for (const [dc, dr] of NEIGHBOUR_STEPS) {
      const nc = c + dc;
      const nr = r + dr;
      if (!inGrid(nc, nr)) continue;
      const key = nr * cols + nc;
      if (seen.has(key) || !isWalkable(nc, nr)) continue;
      seen.add(key);
      want.delete(key);
      queue.push(key);
    }
  }
  return want.size === 0;
}

/** The tile one step in front of a seat. */
function facingTile(seat: { seatCol: number; seatRow: number; facingDir: Direction }): [number, number] {
  switch (seat.facingDir) {
    case Direction.UP:
      return [seat.seatCol, seat.seatRow - 1];
    case Direction.LEFT:
      return [seat.seatCol - 1, seat.seatRow];
    case Direction.RIGHT:
      return [seat.seatCol + 1, seat.seatRow];
    default:
      return [seat.seatCol, seat.seatRow + 1];
  }
}

/**
 * The seats that belong to the RECEPTIONIST'S workstation, never to a real agent: every seat whose facing tile is on the
 * reception desk's footprint, plus the shipped bench seat (`DI_RECEPTION_BENCH_UID`) when it stands within three tiles of the
 * desk (a recycled uid elsewhere is not the bench). Empty when there is no post: a layout without the desk keeps every seat.
 */
export function receptionReservedSeatIds(
  post: ReceptionPost | null,
  seats: ReadonlyMap<string, { seatCol: number; seatRow: number; facingDir: Direction }>,
): Set<string> {
  const reserved = new Set<string>();
  if (!post) return reserved;
  const onDesk = (c: number, r: number): boolean =>
    c >= post.deskCol && c < post.deskCol + post.deskW && r >= post.deskRow && r < post.deskRow + post.deskH;
  for (const [uid, seat] of seats) {
    const [fc, fr] = facingTile(seat);
    if (onDesk(fc, fr)) reserved.add(uid);
  }
  const bench = seats.get(DI_RECEPTION_BENCH_UID);
  if (
    bench &&
    bench.seatCol >= post.deskCol - 3 &&
    bench.seatCol < post.deskCol + post.deskW + 3 &&
    bench.seatRow >= post.deskRow - 3 &&
    bench.seatRow < post.deskRow + post.deskH + 3
  )
    reserved.add(DI_RECEPTION_BENCH_UID);
  return reserved;
}

/**
 * Whether a world-pixel point is on the reception desk or its receptionist: the desk's footprint
 * plus the receptionist's tile and the headroom its sprite stands in. Callers test agents, pets
 * and residents FIRST, so a real character always wins the pointer over the desk.
 */
export function isReceptionHit(
  post: ReceptionPost,
  worldX: number,
  worldY: number,
  tileSize: number,
  headroomPx: number,
): boolean {
  const deskL = post.deskCol * tileSize;
  const deskT = post.deskRow * tileSize;
  const onDesk =
    worldX >= deskL &&
    worldX < deskL + post.deskW * tileSize &&
    worldY >= deskT &&
    worldY < deskT + post.deskH * tileSize;
  const standL = post.standCol * tileSize;
  const standT = post.standRow * tileSize;
  const onStand =
    worldX >= standL &&
    worldX < standL + tileSize &&
    worldY >= standT - headroomPx &&
    worldY < standT + tileSize;
  return onDesk || onStand;
}
