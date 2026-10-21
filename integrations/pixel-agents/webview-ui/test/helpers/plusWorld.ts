/**
 * A SYNTHETIC plus-shaped road world for traffic engine tests: one east-west band (centre row 12) crossing one
 * north-south band (centre column 12) with a 3x3 intersection box at (12, 12), and a zebra crossing across the
 * east-west band at columns 7 and 8. Everything else is VOID. Small enough to reason about every tile, built
 * from the same tile types as the shipped metropolis so the real graph builder derives its lanes.
 *
 * Lanes it yields (right-hand traffic): east lane row 13 (travel east), west lane row 11, south lane column 11,
 * north lane column 13. The east lane's tile index equals its column; its zebra tiles are indices 7 and 8 and
 * its box tiles are 11..13.
 */
import type { TrafficGraph } from '../../src/office/traffic/trafficGraph.ts';
import { buildTrafficGraph } from '../../src/office/traffic/trafficGraph.ts';
import { TileType } from '../../src/office/types.ts';

export const PLUS_COLS = 24;
export const PLUS_ROWS = 24;
export const PLUS_CENTRE = 12;

export function plusTiles(): number[] {
  const tiles = new Array<number>(PLUS_COLS * PLUS_ROWS).fill(TileType.VOID);
  const set = (c: number, r: number, t: number): void => {
    tiles[r * PLUS_COLS + c] = t;
  };
  for (let c = 0; c < PLUS_COLS; c++) {
    set(c, PLUS_CENTRE - 1, TileType.FLOOR_15);
    set(c, PLUS_CENTRE, TileType.FLOOR_16);
    set(c, PLUS_CENTRE + 1, TileType.FLOOR_15);
  }
  for (let r = 0; r < PLUS_ROWS; r++) {
    set(PLUS_CENTRE - 1, r, TileType.FLOOR_15);
    set(PLUS_CENTRE, r, TileType.FLOOR_17);
    set(PLUS_CENTRE + 1, r, TileType.FLOOR_15);
  }
  // the intersection box is plain asphalt
  for (let dy = -1; dy <= 1; dy++)
    for (let dx = -1; dx <= 1; dx++) set(PLUS_CENTRE + dx, PLUS_CENTRE + dy, TileType.FLOOR_15);
  // a zebra crossing across the east-west band, 2 columns wide
  for (const c of [7, 8])
    for (let dy = -1; dy <= 1; dy++) set(c, PLUS_CENTRE + dy, TileType.FLOOR_27);
  return tiles;
}

export function plusGraph(blocked: ReadonlySet<number> = new Set()): TrafficGraph {
  return buildTrafficGraph(PLUS_COLS, PLUS_ROWS, plusTiles(), (t) => blocked.has(t));
}

/** The tile index of (col, row) in the plus world. */
export const tileOf = (col: number, row: number): number => row * PLUS_COLS + col;
