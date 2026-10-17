import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import {
  checkLayoutForm,
  LAYOUT_ID_MAX_LENGTH,
  LAYOUT_MAX_CARPET_CELLS,
  LAYOUT_MAX_CELLS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FRAME_BYTES,
  LAYOUT_MAX_FURNITURE,
} from '../../core/src/layoutLimits.ts';
import { migrateLayout } from '../../core/src/layoutMigration.ts';
import { acceptLayout, prepareImport } from '../src/office/layout/layoutIntake.ts';
import { deserializeLayout, migrateLayoutColors } from '../src/office/layout/layoutSerializer.ts';
import type { OfficeLayout } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const read = (rel: string) => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf-8'));

test('ONE migration: the webview migrates with exactly the function admission runs', () => {
  const legacy = {
    version: 1,
    cols: 3,
    rows: 1,
    tiles: [8, 1, 3],
    furniture: [{ uid: 'a', type: 'chair', col: 0, row: 0 }],
  };
  assert.deepEqual(
    migrateLayoutColors(legacy as unknown as OfficeLayout),
    migrateLayout(legacy).layout,
  );
});

test('migration reports exactly when it changed something (each growth path)', () => {
  const forest = read('packaging/di/worlds/vast-forest.json');
  assert.equal(migrateLayout(forest).changed, false); // already the consumer form: nothing re-measured
  const base = {
    version: 1,
    cols: 2,
    rows: 1,
    layoutRevision: 1,
    tiles: [1, 1],
    tileColors: [null, null],
    furniture: [],
    pets: [],
  };
  assert.equal(migrateLayout(base).changed, false);
  assert.equal(migrateLayout({ ...base, furniture: [{ uid: 'a', type: 'desk' }] }).changed, true); // 1
  assert.equal(migrateLayout({ ...base, layoutRevision: undefined, tiles: [8, 1] }).changed, true); // 2
  assert.equal(migrateLayout({ ...base, pets: undefined }).changed, true); // 3
  assert.equal(migrateLayout({ ...base, tileColors: undefined }).changed, true); // 4 (missing)
  assert.equal(migrateLayout({ ...base, tileColors: [null] }).changed, true); // 4 (wrong length)
  assert.equal(migrateLayout({ ...base, tiles: [8, 1] }).changed, false); // FLOOR_8 kept when revisioned
});

test('layoutLoaded, Import and the deserializer refuse the round-2 reproducer for its MIGRATED size', () => {
  // At the cell cap (the widest grid with exactly LAYOUT_MAX_CELLS cells, both axes within
  // LAYOUT_MAX_DIM), with a label JSON escapes to six bytes per character on the first n cells.
  let cols = LAYOUT_MAX_DIM;
  while (!Number.isInteger(LAYOUT_MAX_CELLS / cols) || LAYOUT_MAX_CELLS / cols > LAYOUT_MAX_DIM)
    cols--;
  const rows = LAYOUT_MAX_CELLS / cols;
  const cells = cols * rows;
  const color = { h: 214, s: 30, b: -100, c: -55, colorize: true };
  const label = '\u0001'.repeat(LAYOUT_ID_MAX_LENGTH);
  /* eslint-disable pixel-agents/no-inline-colors -- area colours as layout test data */
  const areaColor = '#336699';
  /* eslint-enable pixel-agents/no-inline-colors */
  // the furniture ids escape to six bytes per character too: with carpets now capped at
  // LAYOUT_MAX_CARPET_CELLS, they keep the reproducer able to reach the frame ceiling
  const heavyId = (k: number) =>
    String.fromCharCode(1 + (k % 31), 1 + (Math.floor(k / 31) % 31)) +
    '\u0001'.repeat(LAYOUT_ID_MAX_LENGTH - 2);
  const furniture = Array.from({ length: LAYOUT_MAX_FURNITURE }, (_, k) => ({
    uid: heavyId(k),
    type: heavyId(k),
    col: 0,
    row: 0,
  }));
  // no tileColors and no pets: migration generates both
  const reproducer = (labelled: number) => ({
    version: 1,
    cols,
    rows,
    layoutRevision: 3,
    tiles: new Array(cells).fill(7),
    furniture,
    carpetTiles: Array.from({ length: cells }, (_, i) =>
      i < LAYOUT_MAX_CARPET_CELLS ? { variant: 2, color, accentColor: color, order: 1 } : null,
    ),
    areas: [
      { label, color: areaColor },
      { label: 'a', color: areaColor },
    ],
    areaTiles: Array.from({ length: cells }, (_, i) => (i < labelled ? label : 'a')),
  });
  // binary search (no hand-tuned count): the LARGEST raw form its own legacy check admits
  const fits = (n: number) => checkLayoutForm(reproducer(n), { legacy: true }) === null;
  assert.ok(fits(0));
  assert.ok(!fits(cells));
  let lo = 0;
  let hi = cells;
  while (hi - lo > 1) {
    const mid = Math.floor((lo + hi) / 2);
    if (fits(mid)) lo = mid;
    else hi = mid;
  }
  const layout = reproducer(lo);
  assert.equal(checkLayoutForm(layout, { legacy: true }), null); // the raw form fits ...
  const loaded = acceptLayout(layout); // ... the form the office would hold does not
  assert.ok('refused' in loaded);
  assert.match(loaded.refused, new RegExp(`^after migration: .*over ${LAYOUT_MAX_FRAME_BYTES}$`));
  const text = JSON.stringify(layout);
  assert.ok('refused' in prepareImport(text));
  assert.equal(deserializeLayout(text), null);
}, 120_000);
