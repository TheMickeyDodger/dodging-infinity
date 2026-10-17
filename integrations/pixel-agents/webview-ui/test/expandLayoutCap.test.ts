/**
 * The editor's grid growth (expandLayout) honours BOTH layout bounds the server admits: each axis
 * up to MAX_COLS / MAX_ROWS, and the INDEPENDENT total-cell cap MAX_CELLS — so the editor can never
 * build a grid the entry paths would refuse. Every size below is derived from those constants.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import { checkLayout, LAYOUT_MAX_CELLS, LAYOUT_MAX_DIM } from '../../core/src/layoutLimits.ts';
import { MAX_CELLS, MAX_COLS, MAX_ROWS } from '../src/constants.ts';
import type { ExpandDirection } from '../src/office/editor/editorActions.ts';
import { expandLayout } from '../src/office/editor/editorActions.ts';
import type { OfficeLayout, TileType as TileTypeVal } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const DIRECTIONS: ExpandDirection[] = ['left', 'right', 'up', 'down'];

const grid = (cols: number, rows: number): OfficeLayout => ({
  version: 1,
  cols,
  rows,
  tiles: new Array<TileTypeVal>(cols * rows).fill(TileType.FLOOR_1),
  furniture: [{ uid: 'desk', type: 'DESK_FRONT', col: 0, row: 0 }],
  tileColors: new Array(cols * rows).fill(null),
});

/** The widest grid with exactly MAX_CELLS cells, both axes within MAX_COLS / MAX_ROWS. */
const capShape = (): { cols: number; rows: number } => {
  for (let cols = MAX_COLS; cols >= 1; cols--) {
    const rows = MAX_CELLS / cols;
    if (Number.isInteger(rows) && rows <= MAX_ROWS) return { cols, rows };
  }
  throw new Error('no grid within MAX_COLS x MAX_ROWS has exactly MAX_CELLS cells');
};

test('the editor bounds ARE the shared layout bounds (one definition)', () => {
  assert.equal(MAX_COLS, LAYOUT_MAX_DIM);
  assert.equal(MAX_ROWS, LAYOUT_MAX_DIM);
  assert.equal(MAX_CELLS, LAYOUT_MAX_CELLS);
  assert.ok(MAX_CELLS < MAX_COLS * MAX_ROWS); // the cell cap is independent: DIM x DIM is out
});

test('expandLayout refuses to grow past MAX_CELLS even when both axes stay within MAX_COLS / MAX_ROWS', () => {
  // the widest grid within the cap: one more row is still within MAX_ROWS, but over MAX_CELLS
  const widest = grid(MAX_COLS, Math.floor(MAX_CELLS / MAX_COLS));
  assert.ok(widest.rows + 1 <= MAX_ROWS);
  assert.ok(widest.cols * (widest.rows + 1) > MAX_CELLS);
  assert.equal(expandLayout(widest, 'down'), null);
  assert.equal(expandLayout(widest, 'up'), null);
  // ...and its transpose, one more column
  const tallest = grid(Math.floor(MAX_CELLS / MAX_ROWS), MAX_ROWS);
  assert.ok(tallest.cols + 1 <= MAX_COLS);
  assert.equal(expandLayout(tallest, 'right'), null);
  assert.equal(expandLayout(tallest, 'left'), null);
  // a grid at EXACTLY the cell cap, both axes well inside the ceiling, grows in no direction
  const { cols, rows } = capShape();
  assert.ok(cols < MAX_COLS && rows < MAX_ROWS);
  const atCap = grid(cols, rows);
  for (const direction of DIRECTIONS) assert.equal(expandLayout(atCap, direction), null, direction);
});

test('expandLayout refuses to grow past MAX_COLS / MAX_ROWS', () => {
  const fullWidth = grid(MAX_COLS, 1);
  assert.equal(expandLayout(fullWidth, 'right'), null);
  assert.equal(expandLayout(fullWidth, 'left'), null);
  const fullHeight = grid(1, MAX_ROWS);
  assert.equal(expandLayout(fullHeight, 'down'), null);
  assert.equal(expandLayout(fullHeight, 'up'), null);
});

test('expandLayout still grows when under every cap — up to EXACTLY the cell cap and the axis ceiling', () => {
  // an ordinary small grid grows in every direction, shifting content when growing left / up
  const small = grid(2, 1);
  const right = expandLayout(small, 'right');
  assert.ok(right);
  assert.deepEqual([right.layout.cols, right.layout.rows], [3, 1]);
  assert.deepEqual(right.shift, { col: 0, row: 0 });
  const left = expandLayout(small, 'left');
  assert.ok(left);
  assert.deepEqual(left.shift, { col: 1, row: 0 });
  assert.equal(left.layout.tiles[0], TileType.VOID); // the new column is VOID
  assert.deepEqual(left.layout.furniture[0], { uid: 'desk', type: 'DESK_FRONT', col: 1, row: 0 });
  const up = expandLayout(small, 'up');
  assert.ok(up);
  assert.deepEqual([up.layout.cols, up.layout.rows, up.shift.row], [2, 2, 1]);

  // one row short of the cell cap grows into it: the result has exactly MAX_CELLS cells
  const { cols, rows } = capShape();
  const grown = expandLayout(grid(cols, rows - 1), 'down');
  assert.ok(grown);
  assert.equal(grown.layout.cols * grown.layout.rows, MAX_CELLS);
  assert.equal(grown.layout.tiles.length, MAX_CELLS);
  assert.equal(grown.layout.tileColors?.length, MAX_CELLS);
  // ...and what the editor built is admitted by the shared check (the editor never out-builds it)
  assert.equal(checkLayout(grown.layout), null);

  // one column short of the axis ceiling grows to it (cells stay under the cap)
  const wide = expandLayout(grid(MAX_COLS - 1, 1), 'right');
  assert.ok(wide);
  assert.equal(wide.layout.cols, MAX_COLS);
  const tall = expandLayout(grid(1, MAX_ROWS - 1), 'down');
  assert.ok(tall);
  assert.equal(tall.layout.rows, MAX_ROWS);
});
